import json
from uuid import uuid4

from sqlalchemy import select

from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import DeliveryAttempt, TelegramOutbox
from tg_lottery.ops.unknown_review import ReviewRequest, read_manifest


async def seed_unknown(sessions, operation="send_progress_notice"):
    async with sessions() as session, session.begin():
        row = add_outbox(
            session,
            operation=operation,
            aggregate_type="campaign",
            aggregate_id=str(uuid4()),
            recipient_chat_id=-1001234,
            business_key=f"unknown-review:{uuid4()}",
            payload={"text": "review target"},
        )
        await session.flush()
        row.state = OutboxState.UNKNOWN_HOLD
        row.gateway_started_at = utcnow()
        row.last_error = "TelegramNetworkError: ClientConnectorError"
        attempt = DeliveryAttempt(
            outbox_id=row.id,
            telegram_method=operation,
            attempt_number=1,
            certainty=DeliveryCertainty.UNKNOWN,
            error_code="TelegramNetworkError",
            error_description=row.last_error,
            finished_at=utcnow(),
        )
        session.add(attempt)
        await session.flush()
        return row.id


async def frozen_request(sessions, ids):
    async with sessions() as session:
        rows = tuple(
            await session.scalars(select(TelegramOutbox).where(TelegramOutbox.id.in_(ids)))
        )
        records = []
        for row in rows:
            attempt = await session.scalar(
                select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == row.id)
            )
            records.append(_manifest_row(row, attempt))
    raw = ("\n".join(json.dumps(item) for item in records) + "\n").encode()
    manifest, digest = read_manifest(raw)
    return ReviewRequest(manifest, digest, "test-incident", len(records))


def _manifest_row(row, attempt):
    return {
        "outbox_id": str(row.id),
        "operation": row.operation,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "recipient_chat_id": row.recipient_chat_id,
        "state": row.state.value,
        "request_hash": row.request_hash,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "gateway_started_at": row.gateway_started_at.isoformat(),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "attempt_certainty": attempt.certainty.value,
        "attempt_finished_at": attempt.finished_at.isoformat(),
        "error_code": attempt.error_code,
        "error_class": "connector",
    }
