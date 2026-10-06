"""Audit one legacy invite failure against its verified aiogram/aiohttp runtime source."""

import argparse
import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.config import get_settings
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import DeliveryAttempt, TelegramOutbox
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.telegram.delivery_failures import (
    LEGACY_CONNECT_ERROR_PREFIX,
    SINGLE_INVITE_OPERATIONS,
)


@dataclass(frozen=True, kw_only=True)
class RepairOptions:
    outbox_id: UUID
    runtime_ref: str
    repair_ref: str
    expected_fingerprint: str | None = None


async def reconcile_invite_connection(
    sessions: object, options: RepairOptions
) -> dict[str, object]:
    if not options.runtime_ref.strip() or not options.repair_ref.strip():
        raise ValueError("verified runtime source and repair references are required")
    async with sessions() as session, session.begin():
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.id == options.outbox_id).with_for_update()
        )
        attempt = await _verified_attempt(session, row)
        report = _report(row, attempt, options)
        if options.expected_fingerprint is None:
            return report
        if options.expected_fingerprint != report["fingerprint"]:
            raise ValueError("connection-failure reconciliation preview changed")
        add_audit(
            session,
            actor_user_id=None,
            action="outbox.connect_failure_reconciled",
            aggregate_type="outbox",
            aggregate_id=str(row.id),
            reason="Verified connector failure before invite request; no Telegram call or resend",
            details=report,
        )
        row.state = OutboxState.PRE_ACCEPT_FAILED
        row.updated_at = utcnow()
        return report


async def _verified_attempt(session: object, row: TelegramOutbox | None) -> DeliveryAttempt:
    _verify_outbox(row)
    attempt = await session.scalar(
        select(DeliveryAttempt)
        .where(DeliveryAttempt.outbox_id == row.id)
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
        .with_for_update()
    )
    if attempt is None:
        raise ValueError("original delivery attempt is missing")
    if (
        attempt.telegram_method != row.operation
        or attempt.certainty is not DeliveryCertainty.UNKNOWN
    ):
        raise ValueError("original method or delivery certainty does not match")
    if attempt.error_code != "TelegramNetworkError" or attempt.remote_result is not None:
        raise ValueError("original failure is not an unconfirmed Telegram network error")
    description = attempt.error_description or ""
    if description != row.last_error or not description.startswith(LEGACY_CONNECT_ERROR_PREFIX):
        raise ValueError("original serialized ClientConnectorError evidence does not match")
    if attempt.finished_at < row.gateway_started_at:
        raise ValueError("original failure predates its gateway call")
    await _verify_no_other_possible_write(session, row, attempt)
    return attempt


async def _verify_no_other_possible_write(session, row, attempt) -> None:
    other = await session.scalar(
        select(DeliveryAttempt.id)
        .where(
            DeliveryAttempt.outbox_id == row.id,
            DeliveryAttempt.id != attempt.id,
            DeliveryAttempt.certainty != DeliveryCertainty.EXPLICIT_REJECTION,
        )
        .with_for_update()
    )
    if other is not None:
        raise ValueError("another delivery attempt may have reached Telegram")


def _verify_outbox(row: TelegramOutbox | None) -> None:
    if row is None or row.operation not in SINGLE_INVITE_OPERATIONS:
        raise ValueError("target is not a single invite-creation request")
    if row.state is not OutboxState.UNKNOWN_HOLD or row.gateway_started_at is None:
        raise ValueError("target is not awaiting gateway reconciliation")
    if row.lease_owner is not None or row.lease_until is not None:
        raise ValueError("target still has an executor lease")
    if row.remote_message_id is not None or row.encrypted_remote_secret is not None:
        raise ValueError("target already contains remote evidence")
    _verify_request_hash(row)


def _verify_request_hash(row: TelegramOutbox) -> None:
    if row.projected_at is not None or canonical_hash(row.payload) != row.request_hash:
        raise ValueError("target projection or request hash does not match")


def _report(
    row: TelegramOutbox, attempt: DeliveryAttempt, options: RepairOptions
) -> dict[str, object]:
    evidence = {
        "outbox_id": str(row.id),
        "operation": row.operation,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "recipient_chat_id": row.recipient_chat_id,
        "business_key": row.business_key,
        "request_hash": row.request_hash,
        "gateway_started_at": row.gateway_started_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "attempt_created_at": attempt.created_at.isoformat(),
        "attempt_finished_at": attempt.finished_at.isoformat(),
        "original_error_hash": canonical_hash({"error": attempt.error_description}),
        "original_certainty": attempt.certainty.value,
        "runtime_ref": options.runtime_ref,
        "repair_ref": options.repair_ref,
        "before_state": row.state.value,
        "after_state": OutboxState.PRE_ACCEPT_FAILED.value,
        "transport_phase": "connect",
        "request_sent": False,
    }
    return evidence | {
        "fingerprint": canonical_hash(evidence),
        "applied": options.expected_fingerprint is not None,
    }


def _parse_options() -> RepairOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox-id", type=UUID, required=True)
    parser.add_argument("--runtime-ref", required=True)
    parser.add_argument("--repair-ref", required=True)
    parser.add_argument("--expected-fingerprint")
    return RepairOptions(**vars(parser.parse_args()))


async def _run(options: RepairOptions) -> None:
    engine = create_engine(get_settings().database_url)
    try:
        report = await reconcile_invite_connection(create_session_factory(engine), options)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run(_parse_options()))
