from collections.abc import Sequence

from sqlalchemy import select

from tg_lottery.application.campaign_targets import uses_multi_group_contract
from tg_lottery.application.public_participation import public_participation
from tg_lottery.application.records import add_outbox
from tg_lottery.application.rule_publication import prepare_rule_publication
from tg_lottery.domain.public_refs import new_invite_link_name
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignRequirement,
    Group,
    Prize,
)


async def add_publication_outboxes(
    session: object,
    *,
    campaign: Campaign,
    recipient_chat_ids: Sequence[int],
    bot_username: str,
    prizes: Sequence[Prize],
) -> None:
    campaign.presentation = campaign.presentation | {
        "public_participation": await public_participation(session, campaign.id)
    }
    private_chats = await _private_chats(session, campaign)
    if not private_chats:
        _add_campaign_cards(
            session,
            campaign=campaign,
            recipient_chat_ids=recipient_chat_ids,
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
    payload = campaign_card_payload(campaign, bot_username, prizes)
    if campaign.rules.get("participation_rules_version") == "v3.0":
        payload, deferred = prepare_rule_publication(
            session, campaign=campaign, chat_id=recipient_chat_id, payload=payload
        )
        if deferred:
            return
    add_outbox(
        session,
        operation="send_campaign_card",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=recipient_chat_id,
        business_key=(f"publish:{campaign.id}:{campaign.config_revision}:{recipient_chat_id}"),
        payload=payload,
    )


def campaign_card_payload(
    campaign: Campaign, bot_username: str, prizes: Sequence[Prize]
) -> dict[str, object]:
    return {
        "campaign_id": str(campaign.id),
        "campaign_ref": campaign.public_ref,
        "name": campaign.name,
        "preheat_at": campaign.preheat_at.isoformat(),
        "cutoff_at": campaign.cutoff_at.isoformat(),
        "draw_at": campaign.draw_at.isoformat(),
        "timezone": campaign.timezone,
        "bot_username": bot_username,
        "presentation": {
            key: value
            for key, value in campaign.presentation.items()
            if key
            not in {"publication_rule_manifest", "rule_state_intents", "publication_card_texts"}
        },
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
    participation = presentation.get("participation_groups") or []
    extras = presentation.get("required_chats") or []
    main = presentation.get("main_chat")
    values = list(participation) + list(extras)
    if not participation and isinstance(main, dict):
        values.insert(0, main)
    unique = {int(item["id"]): item for item in values if isinstance(item, dict)}
    return tuple(unique.values())


async def _private_chats(session: object, campaign: Campaign) -> tuple[dict[str, object], ...]:
    if not uses_multi_group_contract(campaign):
        return tuple(item for item in _conditions(campaign.presentation) if item.get("private"))
    participation = tuple(
        await session.execute(
            select(CampaignParticipationGroup, Group.chat_id)
            .join(Group, Group.id == CampaignParticipationGroup.group_id)
            .where(
                CampaignParticipationGroup.campaign_id == campaign.id,
                CampaignParticipationGroup.private.is_(True),
            )
            .order_by(CampaignParticipationGroup.position)
        )
    )
    requirements = tuple(
        await session.execute(
            select(CampaignRequirement, Group.chat_id)
            .outerjoin(Group, Group.id == CampaignRequirement.target_group_id)
            .where(
                CampaignRequirement.campaign_id == campaign.id,
                CampaignRequirement.private.is_(True),
            )
            .order_by(CampaignRequirement.position)
        )
    )
    values = ({"id": chat_id} for _, chat_id in participation)
    extras = ({"id": chat_id or row.chat_id_snapshot} for row, chat_id in requirements)
    return tuple(values) + tuple(extras)


def _add_campaign_cards(
    session: object,
    *,
    campaign: Campaign,
    recipient_chat_ids: Sequence[int],
    bot_username: str,
    prizes: Sequence[Prize],
) -> None:
    for chat_id in recipient_chat_ids:
        add_campaign_card_outbox(
            session,
            campaign=campaign,
            recipient_chat_id=chat_id,
            bot_username=bot_username,
            prizes=prizes,
        )
