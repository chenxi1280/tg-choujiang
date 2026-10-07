from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.message_deletion import (
    TEMPORARY_REMINDER_DELETE_SECONDS,
    TEMPORARY_REMINDER_OPERATION,
)
from tg_lottery.application.records import add_outbox
from tg_lottery.infrastructure.db import TelegramOutbox


async def queue_temporary_reminder(
    uow_factory,
    *,
    campaign_id: UUID,
    recipient_chat_id: int,
    text: str,
    business_key: str,
) -> None:
    if not text.strip():
        raise ValueError("temporary reminder text is required")
    payload = {"text": text, "delete_after_seconds": TEMPORARY_REMINDER_DELETE_SECONDS}
    async with uow_factory() as uow:
        existing = await uow.session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
        )
        if existing is not None:
            return
        add_outbox(
            uow.session,
            operation=TEMPORARY_REMINDER_OPERATION,
            aggregate_type="campaign",
            aggregate_id=str(campaign_id),
            recipient_chat_id=recipient_chat_id,
            business_key=business_key,
            payload=payload,
        )
