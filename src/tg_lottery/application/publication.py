from collections.abc import Sequence

from tg_lottery.application.records import add_outbox
from tg_lottery.domain.public_refs import new_invite_link_name
from tg_lottery.infrastructure.db import Campaign, Prize


def add_publication_outboxes(
    session: object,
    *,
    campaign: Campaign,
    recipient_chat_id: int,
    bot_username: str,
    prizes: Sequence[Prize],
) -> None:
    private_chats = tuple(
        item for item in _conditions(campaign.presentation) if item.get("private")
    )
    if not private_chats:
        add_campaign_card_outbox(
            session,
            campaign=campaign,
            recipient_chat_id=recipient_chat_id,
            bot_username=bot_username,
            prizes=prizes,
        )
        return
    for chat in private_chats:
        chat_id = int(chat["id"])
        add_outbox(
            session,
            operation="create_required_chat_invite",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=chat_id,
            business_key=f"required_chat_invite:{campaign.id}:{chat_id}",
            payload={
                "campaign_id": str(campaign.id),
                "chat_id": chat_id,
                "name": new_invite_link_name(),
                "expire_at": campaign.cutoff_at.isoformat(),
                "bot_username": bot_username,
            },
        )


def add_campaign_card_outbox(
    session: object,
    *,
    campaign: Campaign,
    recipient_chat_id: int,
    bot_username: str,
    prizes: Sequence[Prize],
) -> None:
    add_outbox(
        session,
        operation="send_campaign_card",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=recipient_chat_id,
        business_key=f"publish:{campaign.id}:{campaign.config_revision}",
        payload=campaign_card_payload(campaign, bot_username, prizes),
    )


def campaign_card_payload(
    campaign: Campaign, bot_username: str, prizes: Sequence[Prize]
) -> dict[str, object]:
    return {
        "campaign_id": str(campaign.id),
        "campaign_ref": campaign.public_ref,
        "name": campaign.name,
        "cutoff_at": campaign.cutoff_at.isoformat(),
        "draw_at": campaign.draw_at.isoformat(),
        "timezone": campaign.timezone,
        "bot_username": bot_username,
        "presentation": campaign.presentation,
        "rules": campaign.rules,
        "prizes": [
            {
                "position": prize.position,
                "name": prize.name,
                "winner_count": prize.winner_count,
            }
            for prize in prizes
        ],
        "total_winners": sum(prize.winner_count for prize in prizes),
    }


def _conditions(presentation: dict[str, object]) -> tuple[dict[str, object], ...]:
    main = presentation.get("main_chat")
    extras = presentation.get("required_chats") or []
    values = ([main] if isinstance(main, dict) else []) + list(extras)
    return tuple(item for item in values if isinstance(item, dict))
