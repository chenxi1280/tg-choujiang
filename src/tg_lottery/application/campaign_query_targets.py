from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select

from tg_lottery.application.campaign_targets import (
    QualificationTarget,
    qualification_targets,
)
from tg_lottery.application.invitation_models import required_chat_ids
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignRequirement,
    Group,
)

_TERMINAL_STATES = (
    CampaignState.DRAWN,
    CampaignState.ARCHIVED,
    CampaignState.CANCELLED,
)


async def load_campaign_targets(
    session: object, campaign_id: UUID
) -> tuple[QualificationTarget, ...]:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise ValueError("campaign not found")
    return await qualification_targets(session, campaign)


async def mandatory_chat_ids(session: object, campaign: Campaign) -> tuple[int, ...]:
    targets = await load_campaign_targets(session, campaign.id)
    if targets:
        return tuple(target.chat_id for target in targets)
    group = await session.get(Group, campaign.group_id)
    if group is None:
        return ()
    rules = CampaignRules.model_validate(campaign.rules)
    return required_chat_ids(group.chat_id, rules)


async def campaigns_for_mandatory_chat(session: object, chat_id: int) -> tuple[UUID, ...]:
    participation = await session.scalars(
        select(CampaignParticipationGroup.campaign_id)
        .join(Campaign, Campaign.id == CampaignParticipationGroup.campaign_id)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            or_(
                CampaignParticipationGroup.chat_id_snapshot == chat_id,
                Group.chat_id == chat_id,
            ),
            Campaign.state.not_in(_TERMINAL_STATES),
        )
    )
    requirements = await session.scalars(
        select(CampaignRequirement.campaign_id)
        .join(Campaign, Campaign.id == CampaignRequirement.campaign_id)
        .outerjoin(Group, Group.id == CampaignRequirement.target_group_id)
        .where(
            or_(
                CampaignRequirement.chat_id_snapshot == chat_id,
                Group.chat_id == chat_id,
            ),
            Campaign.state.not_in(_TERMINAL_STATES),
        )
    )
    return tuple(dict.fromkeys((*participation, *requirements)))


async def active_campaign_by_chat(session: object, chat_id: int) -> Campaign | None:
    campaign = await session.scalar(
        select(Campaign)
        .join(
            CampaignParticipationGroup,
            CampaignParticipationGroup.campaign_id == Campaign.id,
        )
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            or_(
                Group.chat_id == chat_id,
                CampaignParticipationGroup.chat_id_snapshot == chat_id,
            ),
            Campaign.state.not_in(_TERMINAL_STATES),
        )
    )
    return campaign or await _legacy_active_campaign_by_chat(session, chat_id)


async def _legacy_active_campaign_by_chat(session: object, chat_id: int) -> Campaign | None:
    return await session.scalar(
        select(Campaign)
        .join(Group, Group.id == Campaign.group_id)
        .where(
            Group.chat_id == chat_id,
            Campaign.state.not_in(_TERMINAL_STATES),
        )
    )
