"""Verify the original, confirmed reminder before retrying its deletion."""

from datetime import datetime, timedelta

from sqlalchemy import select

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import DeliveryCertainty, JobState, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import DeliveryAttempt, ScheduledJob, TelegramOutbox


async def verified_deletion_source(session: object, row: TelegramOutbox) -> dict[str, object]:
    if row.operation != "delete_message" or set(row.payload) != {"message_id"}:
        raise ValueError("retry is permitted only for an exact reminder deletion")
    message_id = row.payload["message_id"]
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        raise ValueError("deletion message id is invalid")
    sources = tuple(
        await session.scalars(
            select(TelegramOutbox)
            .where(
                TelegramOutbox.operation == "send_temporary_reminder",
                TelegramOutbox.state == OutboxState.CONFIRMED,
                TelegramOutbox.recipient_chat_id == row.recipient_chat_id,
                TelegramOutbox.remote_message_id == message_id,
                TelegramOutbox.aggregate_id == row.aggregate_id,
            )
            .with_for_update()
        )
    )
    if len(sources) != 1:
        raise ValueError("exactly one confirmed source reminder is required")
    source = sources[0]
    _verify_source(source)
    attempt = await session.scalar(
        select(DeliveryAttempt)
        .where(DeliveryAttempt.outbox_id == source.id)
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
        .with_for_update()
    )
    _verify_confirmation(attempt, message_id)
    key = f"delete_message:{row.recipient_chat_id}:{message_id}"
    job = await session.scalar(
        select(ScheduledJob).where(ScheduledJob.business_key == key).with_for_update()
    )
    _verify_job(job, row, message_id, confirmed_at=attempt.finished_at)
    return {
        "outbox_id": str(source.id),
        "request_hash": source.request_hash,
        "gateway_started_at": source.gateway_started_at.isoformat(),
        "projected_at": source.projected_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
        "message_id": message_id,
        "confirmation_attempt_id": str(attempt.id),
        "confirmed_at": attempt.finished_at.isoformat(),
        "job_id": str(job.id),
        "job_updated_at": job.updated_at.isoformat(),
        "job_run_at": job.run_at.isoformat(),
    }


def _verify_source(source: TelegramOutbox) -> None:
    if source.projected_at is None or source.gateway_started_at is None:
        raise ValueError("source reminder was not confirmed and projected")
    if source.payload.get("delete_after_seconds") != 15:
        raise ValueError("source reminder did not require deletion after 15 seconds")
    if canonical_hash(source.payload) != source.request_hash:
        raise ValueError("source reminder request hash changed")
    if source.gateway_started_at <= utcnow() - timedelta(hours=47):
        raise ValueError("source reminder is too old for a safe deletion retry")


def _verify_confirmation(attempt: DeliveryAttempt | None, message_id: int) -> None:
    if attempt is None or attempt.certainty is not DeliveryCertainty.CONFIRMED:
        raise ValueError("source reminder has no confirmed attempt")
    if attempt.telegram_method != "send_temporary_reminder":
        raise ValueError("source confirmation method does not match")
    if (attempt.remote_result or {}).get("message_id") != message_id:
        raise ValueError("source confirmation message id does not match")


def _verify_job(
    job: ScheduledJob | None, row: TelegramOutbox, message_id: int, *, confirmed_at: datetime
) -> None:
    if (
        job is None
        or job.job_type != "delete_telegram_message"
        or job.state is not JobState.SUCCEEDED
    ):
        raise ValueError("original reminder deletion job is missing or incomplete")
    if job.aggregate_id != row.aggregate_id or job.run_at > utcnow():
        raise ValueError("deletion job identity or due time does not match")
    if job.payload != {"chat_id": row.recipient_chat_id, "message_id": message_id}:
        raise ValueError("deletion job target does not match")
    if job.run_at != confirmed_at + timedelta(seconds=15) or row.gateway_started_at < job.run_at:
        raise ValueError("deletion job confirmation or timing does not match")
