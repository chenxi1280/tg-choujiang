from datetime import datetime

from sqlalchemy import select

from tg_lottery.application.records import add_outbox
from tg_lottery.application.rule_publication import queue_rule_states
from tg_lottery.infrastructure.db import Campaign, CampaignParticipationGroup, Group
from tg_lottery.presentation.campaign_state_times import campaign_state_times


def add_campaign_card_state(
    session: object,
    campaign: Campaign,
    recipient_chat_id: int,
    *,
    state: str,
    winners: list[dict[str, object]] | None = None,
    completed_at: datetime | None = None,
    message_id: int | None = None,
    rule_chat_id: int | None = None,
) -> None:
    remote_message_id = message_id or campaign.published_message_id
    if remote_message_id is None:
        return
    payload: dict[str, object] = {
        "campaign_name": campaign.name,
        "message_id": remote_message_id,
        "has_cover": bool(campaign.presentation.get("cover_file_id")),
        "card_state": state,
        "time_summary": campaign_state_times(
            cutoff_at=campaign.cutoff_at,
            draw_at=campaign.draw_at,
            timezone=campaign.timezone,
            completed_at=completed_at,
        ),
        **_rule_text_payload(campaign, rule_chat_id or recipient_chat_id),
    }
    if winners is not None:
        payload["winners"] = winners
    add_outbox(
        session,
        operation="edit_campaign_card_state",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=recipient_chat_id,
        business_key=f"campaign_card_state:{campaign.id}:{state}:{recipient_chat_id}",
        payload=payload,
    )


def add_cancelled_campaign_card(
    session: object,
    campaign: Campaign,
    recipient_chat_id: int,
    *,
    message_id: int | None = None,
    rule_chat_id: int | None = None,
) -> None:
    remote_message_id = message_id or campaign.published_message_id
    if remote_message_id is None:
        return
    add_outbox(
        session,
        operation="edit_campaign_card_cancelled",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=recipient_chat_id,
        business_key=f"campaign_card_cancelled:{campaign.id}:{remote_message_id}",
        payload={
            "campaign_name": campaign.name,
            "message_id": remote_message_id,
            "has_cover": bool(campaign.presentation.get("cover_file_id")),
            **_rule_text_payload(campaign, rule_chat_id or recipient_chat_id),
        },
    )


async def add_campaign_card_state_fanout(
    session: object,
    campaign: Campaign,
    *,
    state: str,
    winners: list[dict[str, object]] | None = None,
    completed_at: datetime | None = None,
) -> None:
    queue_rule_states(session, campaign, state=state, completed_at=completed_at)
    rows = await _published_targets(session, campaign.id)
    if not rows:
        group = await session.get(Group, campaign.group_id)
        if group is not None:
            add_campaign_card_state(
                session,
                campaign,
                group.chat_id,
                state=state,
                winners=winners,
                completed_at=completed_at,
            )
        return
    for target, chat_id in rows:
        add_campaign_card_state(
            session,
            campaign,
            chat_id,
            state=state,
            winners=winners,
            completed_at=completed_at,
            message_id=target.published_message_id,
            rule_chat_id=target.chat_id_snapshot,
        )


async def add_cancelled_campaign_card_fanout(session: object, campaign: Campaign) -> None:
    queue_rule_states(session, campaign, state="cancelled")
    rows = await _published_targets(session, campaign.id)
    if not rows:
        group = await session.get(Group, campaign.group_id)
        if group is not None:
            add_cancelled_campaign_card(session, campaign, group.chat_id)
        return
    for target, chat_id in rows:
        add_cancelled_campaign_card(
            session,
            campaign,
            chat_id,
            message_id=target.published_message_id,
            rule_chat_id=target.chat_id_snapshot,
        )


async def _published_targets(session: object, campaign_id: object) -> tuple[object, ...]:
    rows = await session.execute(
        select(CampaignParticipationGroup, Group.chat_id)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            CampaignParticipationGroup.publication_state == "confirmed",
            CampaignParticipationGroup.published_message_id.is_not(None),
        )
        .order_by(CampaignParticipationGroup.position)
    )
    return tuple(rows)


def _rule_text_payload(campaign, chat_id):
    text = (campaign.presentation.get("publication_card_texts") or {}).get(str(chat_id))
    return {"frozen_rules_text": text} if text else {}
