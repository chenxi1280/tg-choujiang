from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.application.campaign_targets import qualification_targets
from tg_lottery.application.invitation_models import required_chat_ids
from tg_lottery.domain.enums import CampaignState, InvitationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Group,
    Invitation,
)


def mark_terminal_validation(invitation: Invitation, campaign: Campaign) -> bool:
    if invitation.due_at is None or invitation.due_at > campaign.cutoff_at:
        invitation.state = InvitationState.EXPIRED_UNQUALIFIED
        invitation.final_reason = "retention_after_cutoff"
        return True
    if campaign.state not in {CampaignState.RUNNING, CampaignState.SETTLING}:
        invitation.state = InvitationState.EXPIRED_UNQUALIFIED
        invitation.final_reason = "campaign_not_running"
        return True
    terminal_states = {
        InvitationState.LEFT_BEFORE_VALID,
        InvitationState.INVALID,
        InvitationState.EXPIRED_UNQUALIFIED,
    }
    return invitation.state in terminal_states


async def validation_chat_ids(
    session: AsyncSession,
    *,
    campaign: Campaign,
    target: CampaignParticipationGroup | None,
) -> tuple[int, ...]:
    if target is not None:
        return (target.chat_id_snapshot,)
    targets = await qualification_targets(session, campaign)
    if targets:
        return tuple(item.chat_id for item in targets)
    group = await session.get(Group, campaign.group_id)
    if group is None:
        raise ValueError("invitation group is missing")
    rules = CampaignRules.model_validate(campaign.rules)
    return required_chat_ids(group.chat_id, rules)
