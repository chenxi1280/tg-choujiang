"""An auxiliary entry follows only a confirmed, explicitly published channel card."""

from uuid import UUID, uuid4

from sqlalchemy import select

from tg_lottery.application.records import add_outbox
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox


async def queue_channel_shortcut(session, source):
    if source.operation not in {"send_campaign_card", "send_featured_campaign"}:
        return
    if source.state != OutboxState.CONFIRMED or source.remote_message_id is None:
        return
    campaign = await session.get(Campaign, UUID(source.aggregate_id), with_for_update=True)
    if not campaign or campaign.presentation.get("ux_policy_version") != "r14":
        return
    if not _channel_target(campaign, source):
        return
    key = f"channel_shortcut:{campaign.id}:{source.recipient_chat_id}"
    if await session.scalar(select(TelegramOutbox.id).where(TelegramOutbox.business_key == key)):
        return
    identifier = uuid4()
    row = add_outbox(
        session,
        operation="send_channel_shortcut",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=source.recipient_chat_id,
        business_key=key,
        payload={
            "campaign_ref": campaign.public_ref,
            "source_outbox_id": str(source.id),
            "bot_username": source.payload["bot_username"],
            "shortcut_id": identifier.hex,
        },
    )
    row.id = identifier


def _channel_target(campaign, source):
    if source.operation == "send_featured_campaign":
        return True
    chats = campaign.presentation.get("participation_groups") or []
    chats = [*chats, campaign.presentation.get("main_chat") or {}]
    return any(
        c.get("id") == source.recipient_chat_id and c.get("type") == "channel" for c in chats
    )
