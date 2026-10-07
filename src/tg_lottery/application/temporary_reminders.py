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
    campaign_ref: str | None = None,
) -> None:
    async with uow_factory() as uow:
        await add_temporary_reminder(
            uow.session,
            campaign_id=campaign_id,
            recipient_chat_id=recipient_chat_id,
            text=text,
            business_key=business_key,
            campaign_ref=campaign_ref,
        )


async def add_temporary_reminder(
    session,
    *,
    campaign_id: UUID,
    recipient_chat_id: int,
    text: str,
    business_key: str,
    campaign_ref: str | None = None,
    user_id: int | None = None,
    observed_at: str | None = None,
) -> None:
    if not text.strip():
        raise ValueError("temporary reminder text is required")
    payload = {"text": text, "delete_after_seconds": TEMPORARY_REMINDER_DELETE_SECONDS}
    if campaign_ref is not None:
        payload["campaign_ref"] = campaign_ref
    if user_id is not None:
        payload["user_id"] = user_id
    if observed_at is not None:
        payload["observed_at"] = observed_at
    existing = await session.scalar(
        select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
    )
    if existing is not None:
        return
    add_outbox(
        session,
        operation=TEMPORARY_REMINDER_OPERATION,
        aggregate_type="campaign",
        aggregate_id=str(campaign_id),
        recipient_chat_id=recipient_chat_id,
        business_key=business_key,
        payload=payload,
    )
