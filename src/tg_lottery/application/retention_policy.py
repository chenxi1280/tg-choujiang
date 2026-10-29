"""Persist one activation boundary; only post-boundary progress is disposable."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from tg_lottery.application.outbox_claims import lock_presend_claim
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import (
    Campaign,
    DeliveryAttempt,
    Participant,
    RuntimePolicy,
    TelegramOutbox,
)

POLICY_KEY = "lottery-ux-r14"
PROGRESS_OPERATION = "send_progress_notice"


async def activate_retention(session_factory):
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(RuntimePolicy)
            .values(key=POLICY_KEY, activated_at=utcnow())
            .on_conflict_do_nothing(index_elements=["key"])
        )


async def apply_retention(session, row):
    if row.operation not in {PROGRESS_OPERATION, "send_temporary_reminder"}:
        return
    policy = await session.get(RuntimePolicy, POLICY_KEY)
    if policy is None:
        return
    campaign = await _notice_campaign(session, row)
    if campaign is None:
        raise ValueError("retention campaign missing")
    modern = campaign.presentation.get("ux_policy_version") == "r14"
    if not is_progress_notice(row) and not modern:
        return
    seconds = campaign.presentation.get("completion_delete_after_seconds", 20) if modern else 20
    payload = dict(row.payload)
    payload.pop("delete_after_seconds", None)
    if seconds is not None:
        payload["delete_after_seconds"] = seconds
    payload["retention_policy_at"] = policy.activated_at.isoformat()
    row.payload = payload
    row.request_hash = canonical_hash(payload)


async def confirmed_send_time(session, row):
    attempt = await session.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.outbox_id == row.id,
            DeliveryAttempt.certainty == DeliveryCertainty.CONFIRMED,
        )
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )
    if attempt is None:
        return None
    raw = (attempt.remote_result or {}).get("date")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, UTC)
    if isinstance(raw, str):
        return datetime.fromisoformat(raw).astimezone(UTC)
    return None


async def progress_retired(session, row):
    if not is_progress_notice(row):
        return False
    policy = await session.get(RuntimePolicy, POLICY_KEY)
    if policy is None:
        return False
    if (
        row.payload.get("retention_policy_at")
        and row.gateway_started_at
        and row.gateway_started_at >= policy.activated_at
    ):
        return False
    sent = await confirmed_send_time(session, row)
    if sent is None:
        raise ValueError("progress send time unknown; deletion requires reconciliation")
    if sent >= policy.activated_at:
        return False
    if sent + timedelta(seconds=1) <= policy.activated_at:
        return True
    raise ValueError("progress send second overlaps activation; deletion requires reconciliation")


async def retired_deletion(session, *, chat_id, message_id):
    source = await session.scalar(
        select(TelegramOutbox).where(
            TelegramOutbox.recipient_chat_id == chat_id,
            TelegramOutbox.remote_message_id == message_id,
            TelegramOutbox.operation.in_([PROGRESS_OPERATION, "send_temporary_reminder"]),
        )
    )
    return source is not None and await progress_retired(session, source)


async def cancel_retired_deletion(session_factory, row):
    if row.operation != "delete_message":
        return False
    async with session_factory() as session, session.begin():
        if not await retired_deletion(
            session, chat_id=row.recipient_chat_id, message_id=int(row.payload["message_id"])
        ):
            return False
        stored = await lock_presend_claim(session, row)
        stored.state = OutboxState.CANCELLED
        stored.last_error = "retention_r14: pre-activation progress deletion abandoned"
        stored.lease_owner = None
        stored.lease_until = None
        return True


async def _notice_campaign(session, row):
    if row.aggregate_type == "participant":
        participant = await session.get(Participant, UUID(row.aggregate_id))
        return await session.get(Campaign, participant.campaign_id) if participant else None
    return await session.get(Campaign, UUID(row.aggregate_id))


def is_progress_notice(row):
    return row.operation == PROGRESS_OPERATION or (
        row.operation == "send_temporary_reminder"
        and row.business_key.startswith("blocked_speech:")
    )
