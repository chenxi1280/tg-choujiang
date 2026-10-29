from datetime import timedelta

from sqlalchemy import select

from tg_lottery.application.records import add_job
from tg_lottery.application.retention_policy import (
    apply_retention,
    is_progress_notice,
    progress_retired,
)
from tg_lottery.domain.enums import DeliveryCertainty
from tg_lottery.infrastructure.db import DeliveryAttempt, ScheduledJob

TEMPORARY_REMINDER_OPERATION = "send_temporary_reminder"
TEMPORARY_REMINDER_DELETE_SECONDS = 15


async def project_message_deletion(session, row) -> None:
    if is_progress_notice(row):
        if await progress_retired(session, row):
            return
        await apply_retention(session, row)
    if "delete_after_seconds" not in row.payload:
        return
    seconds = row.payload["delete_after_seconds"]
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise ValueError("message deletion delay must be a positive integer")
    if row.remote_message_id is None:
        raise RuntimeError("confirmed reminder delivery omitted remote message id")
    business_key = f"delete_message:{row.recipient_chat_id}:{row.remote_message_id}"
    existing = await session.scalar(
        select(ScheduledJob.id).where(ScheduledJob.business_key == business_key)
    )
    if existing is not None:
        return
    confirmed_at = await session.scalar(
        select(DeliveryAttempt.finished_at)
        .where(
            DeliveryAttempt.outbox_id == row.id,
            DeliveryAttempt.certainty == DeliveryCertainty.CONFIRMED,
        )
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )
    if confirmed_at is None:
        raise RuntimeError("message deletion requires a confirmed delivery attempt")
    add_job(
        session,
        job_type="delete_telegram_message",
        aggregate_id=row.aggregate_id,
        business_key=business_key,
        run_at=confirmed_at + timedelta(seconds=seconds),
        payload={
            "chat_id": row.recipient_chat_id,
            "message_id": row.remote_message_id,
            "source_outbox_id": str(row.id),
        },
    )
