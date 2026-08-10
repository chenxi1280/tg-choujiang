from tg_lottery.application.records import add_outbox
from tg_lottery.infrastructure.db import Campaign


def add_campaign_card_state(
    session: object,
    campaign: Campaign,
    recipient_chat_id: int,
    *,
    state: str,
) -> None:
    if campaign.published_message_id is None:
        return
    add_outbox(
        session,
        operation="edit_campaign_card_state",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=recipient_chat_id,
        business_key=f"campaign_card_state:{campaign.id}:{state}",
        payload={
            "campaign_name": campaign.name,
            "message_id": campaign.published_message_id,
            "has_cover": bool(campaign.presentation.get("cover_file_id")),
            "card_state": state,
        },
    )


def add_cancelled_campaign_card(
    session: object, campaign: Campaign, recipient_chat_id: int
) -> None:
    if campaign.published_message_id is None:
        return
    add_outbox(
        session,
        operation="edit_campaign_card_cancelled",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=recipient_chat_id,
        business_key=f"campaign_card_cancelled:{campaign.id}:{campaign.published_message_id}",
        payload={
            "campaign_name": campaign.name,
            "message_id": campaign.published_message_id,
            "has_cover": bool(campaign.presentation.get("cover_file_id")),
        },
    )
