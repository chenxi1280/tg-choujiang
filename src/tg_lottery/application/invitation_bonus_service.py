from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from tg_lottery.application.invitation_rewards import (
    add_invite_bonus,
    group_invite_bonus_target,
    invite_bonus_target,
)
from tg_lottery.domain.enums import InvitationState, TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Invitation,
    InviteeActivityEvent,
    TicketLedger,
)


async def invitation_activity_requirement_met(
    session: object, invitation: Invitation, rules: CampaignRules
) -> bool:
    if not rules.invite.require_activity:
        return True
    count = await session.scalar(
        select(func.count(InviteeActivityEvent.id)).where(
            InviteeActivityEvent.invitation_id == invitation.id,
            InviteeActivityEvent.accepted.is_(True),
        )
    )
    return int(count or 0) >= rules.invite.required_messages


async def award_invitation_bonus(
    session: object,
    *,
    invitation: Invitation,
    campaign: Campaign,
    rules: CampaignRules,
) -> None:
    if not rules.invite_bonus_enabled:
        return
    target = await _participation_target(session, invitation)
    group_rule = rules.invite_rule_for(target.chat_id_snapshot) if target else None
    if group_rule or rules.invite.mode == "ratio" or rules.invite.reward_tiers:
        await award_invitation_target(
            session,
            invitation=invitation,
            campaign=campaign,
            rules=rules,
            occurred_at=invitation.due_at or invitation.joined_at,
        )
        return
    await _award_legacy_bonus(session, invitation=invitation, campaign=campaign, rules=rules)


async def award_invitation_target(
    session: object,
    *,
    invitation: Invitation,
    campaign: Campaign,
    rules: CampaignRules,
    occurred_at: datetime | None,
) -> None:
    invite_count = await _valid_invite_count(session, invitation=invitation, campaign=campaign)
    target = await _bonus_target(session, invitation, rules, invite_count=invite_count)
    current = await _current_bonus(session, invitation=invitation, campaign=campaign)
    delta = target - current
    if delta == 0:
        return
    if delta < 0:
        raise ValueError("confirmed invitation bonus target cannot decrease")
    await add_invite_bonus(
        session,
        invitation=invitation,
        campaign=campaign,
        reward=delta,
        business_key=(
            f"invite_bonus:{campaign.id}:{invitation.id}:"
            f"{invitation.participation_group_id}:"
            f"{invitation.validation_version}:valid"
        ),
        occurred_at=occurred_at,
    )


async def _valid_invite_count(
    session: object,
    *,
    invitation: Invitation,
    campaign: Campaign,
) -> int:
    filters = [
        Invitation.campaign_id == campaign.id,
        Invitation.inviter_participant_id == invitation.inviter_participant_id,
        Invitation.id != invitation.id,
        Invitation.state == InvitationState.VALID,
    ]
    if invitation.participation_group_id is not None:
        filters.append(Invitation.participation_group_id == invitation.participation_group_id)
    count = int(await session.scalar(select(func.count(Invitation.id)).where(*filters)) or 0)
    return count + 1


async def _current_bonus(session: object, *, invitation: Invitation, campaign: Campaign) -> int:
    filters = [
        TicketLedger.campaign_id == campaign.id,
        TicketLedger.participant_id == invitation.inviter_participant_id,
        TicketLedger.source_type.in_([TicketSource.INVITATION, TicketSource.REVERSAL]),
    ]
    if invitation.participation_group_id is not None:
        filters.append(TicketLedger.participation_group_id == invitation.participation_group_id)
    value = await session.scalar(
        select(func.coalesce(func.sum(TicketLedger.delta), 0)).where(*filters)
    )
    return int(value or 0)


async def _bonus_target(
    session: object,
    invitation: Invitation,
    rules: CampaignRules,
    *,
    invite_count: int,
) -> int:
    target = await _participation_target(session, invitation)
    if target is None:
        return invite_bonus_target(rules, invite_count)
    group_rule = rules.invite_rule_for(target.chat_id_snapshot)
    if group_rule is None:
        return invite_bonus_target(rules, invite_count)
    return group_invite_bonus_target(group_rule, invite_count)


async def _participation_target(
    session: object, invitation: Invitation
) -> CampaignParticipationGroup | None:
    if invitation.participation_group_id is None:
        return None
    target = await session.get(CampaignParticipationGroup, invitation.participation_group_id)
    if target is None or target.campaign_id != invitation.campaign_id:
        raise ValueError("invitation participation group is missing")
    return target


async def _award_legacy_bonus(
    session: object,
    *,
    invitation: Invitation,
    campaign: Campaign,
    rules: CampaignRules,
) -> None:
    business_key = f"invite:{invitation.id}:{invitation.validation_version}"
    existing = await session.scalar(
        select(TicketLedger.id).where(TicketLedger.business_key == business_key)
    )
    if existing:
        return
    await add_invite_bonus(
        session,
        invitation=invitation,
        campaign=campaign,
        reward=rules.invite.reward_tickets,
        business_key=business_key,
        occurred_at=invitation.due_at,
    )
