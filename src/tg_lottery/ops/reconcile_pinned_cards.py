"""Reconcile a sent campaign card using its authenticated Telegram pinned-message update."""

from uuid import UUID

from aiogram.types import Message
from sqlalchemy import func, select

from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import Campaign, DeliveryAttempt, TelegramOutbox, TelegramUpdate
from tg_lottery.telegram.outbox_records import add_delivery_attempt, sanitized_remote_result


async def reconcile_pinned_card(
    sessions: object,
    *,
    outbox_id: UUID,
    source_update_id: int,
    bot_id: int,
    actor_user_id: int,
    expected_fingerprint: str | None = None,
) -> dict[str, object]:
    async with sessions() as session, session.begin():
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.id == outbox_id).with_for_update()
        )
        campaign, update, pinned = await _verified_source(
            session, row, source_update_id, bot_id=bot_id
        )
        fingerprint = canonical_hash(
            {
                "id": str(row.id),
                "request_hash": row.request_hash,
                "updated_at": row.updated_at.isoformat(),
                "state": row.state.value,
                "source_update_id": update.update_id,
                "source_hash": update.payload_hash,
                "source_payload_hash": canonical_hash(update.payload),
                "message_id": pinned["message_id"],
            }
        )
        report = {
            "outbox_id": str(row.id),
            "campaign": campaign.name,
            "message_id": pinned["message_id"],
            "source_update_id": update.update_id,
            "fingerprint": fingerprint,
            "applied": expected_fingerprint is not None,
        }
        if expected_fingerprint is None:
            return report
        if expected_fingerprint != fingerprint:
            raise ValueError("reconciliation preview changed")
        await _apply_fact(session, row=row, pinned=pinned, report=report, actor=actor_user_id)
        return report


async def _verified_source(session: object, row: TelegramOutbox, update_id: int, *, bot_id: int):
    await _require_serialization_failure(session, row)
    campaign = await session.get(Campaign, UUID(row.aggregate_id))
    update = await session.scalar(
        select(TelegramUpdate).where(
            TelegramUpdate.update_id == update_id, TelegramUpdate.bot_id == bot_id
        )
    )
    if campaign is None or update is None or update.payload is None:
        raise ValueError("campaign or Telegram source update is missing")
    pinned = (update.payload.get("message") or {}).get("pinned_message") or {}
    _verify_pinned_identity(row, campaign=campaign, update=update, pinned=pinned)
    return campaign, update, pinned


async def _require_serialization_failure(session: object, row: TelegramOutbox) -> None:
    if row is None or row.operation != "send_campaign_card":
        raise ValueError("target is not a campaign-card outbox")
    if row.state not in {OutboxState.PRE_ACCEPT_FAILED, OutboxState.UNKNOWN_HOLD}:
        raise ValueError("outbox is not awaiting reconciliation")
    latest = await session.scalar(
        select(DeliveryAttempt)
        .where(DeliveryAttempt.outbox_id == row.id)
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )
    if latest is None or latest.error_code != "PydanticSerializationError":
        raise ValueError("outbox does not have the known response-serialization failure")


def _verify_pinned_identity(row, *, campaign, update, pinned) -> None:
    message = Message.model_validate(pinned)
    if update.routing_chat_id != row.recipient_chat_id or message.chat.id != row.recipient_chat_id:
        raise ValueError("pinned message belongs to another chat")
    if message.from_user is None or message.from_user.id != update.bot_id:
        raise ValueError("pinned message was not sent by the polling bot")
    if not _has_campaign_button(message, campaign.public_ref):
        raise ValueError("pinned message does not identify this campaign")
    if message.message_id < 1:
        raise ValueError("pinned message has no valid message identity")
    if row.gateway_started_at is None or message.date.timestamp() < int(
        row.gateway_started_at.timestamp()
    ):
        raise ValueError("pinned message predates the gateway attempt")


def _has_campaign_button(message: Message, reference: str) -> bool:
    if message.reply_markup is None:
        return False
    return any(
        button.callback_data == f"member:join:{reference}"
        for line in message.reply_markup.inline_keyboard
        for button in line
    )


async def _apply_fact(session: object, *, row, pinned, report, actor: int) -> None:
    attempt = await session.scalar(
        select(func.max(DeliveryAttempt.attempt_number)).where(DeliveryAttempt.outbox_id == row.id)
    )
    add_delivery_attempt(
        session,
        row,
        attempt_number=int(attempt or 0) + 1,
        certainty=DeliveryCertainty.CONFIRMED,
        remote_result=sanitized_remote_result(
            pinned | {"reconciled_from_update_id": report["source_update_id"]}
        ),
    )
    row.state = OutboxState.CONFIRMED
    row.remote_message_id = pinned["message_id"]
    row.last_error = None
    row.lease_owner = None
    row.lease_until = None
    row.updated_at = utcnow()
    add_audit(
        session,
        actor_user_id=actor,
        action="outbox.remote_fact_reconciled",
        aggregate_type="outbox",
        aggregate_id=str(row.id),
        reason="Telegram pinned-message evidence confirms original delivery; no resend",
        details=report,
    )
