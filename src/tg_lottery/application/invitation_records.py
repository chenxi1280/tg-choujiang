from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.group_membership_facts import observe_existing_member
from tg_lottery.application.invitation_models import JoinObservation, campaign_accepts_attribution
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import InvitationState
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignReferralRoute,
    Invitation,
    InviteLink,
    Participant,
)


async def link_and_campaign(session: object, invite_link_id: UUID) -> tuple[InviteLink, Campaign]:
    link = await session.get(InviteLink, invite_link_id)
    if link is None:
        raise ValueError("invite link was not created by this bot")
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == link.campaign_id).with_for_update()
    )
    if campaign is None:
        raise ValueError("invite link campaign is missing")
    return link, campaign


async def matches_target_group(session: object, link: InviteLink, chat_id: int | None) -> bool:
    if link.participation_group_id is None or chat_id is None:
        return True
    target = await session.get(CampaignParticipationGroup, link.participation_group_id)
    if target is None:
        raise ValueError("invite link participation group is missing")
    return target.chat_id_snapshot == chat_id


async def participation_target(
    session: object, invitation: Invitation
) -> CampaignParticipationGroup | None:
    if invitation.participation_group_id is None:
        return None
    target = await session.get(CampaignParticipationGroup, invitation.participation_group_id)
    if target is None or target.campaign_id != invitation.campaign_id:
        raise ValueError("invitation participation group is missing")
    return target


async def is_self_invite(session: object, link: InviteLink, invitee_user_id: int) -> bool:
    inviter_user_id = await session.scalar(
        select(Participant.user_id).where(Participant.id == link.participant_id)
    )
    if inviter_user_id is None:
        raise ValueError("invite link participant is missing")
    return inviter_user_id == invitee_user_id


async def upsert_candidate(
    session: object,
    *,
    link: InviteLink,
    campaign: Campaign,
    observation: JoinObservation,
) -> Invitation:
    invitation = await session.scalar(
        select(Invitation)
        .where(
            Invitation.campaign_id == campaign.id,
            Invitation.participation_group_id == link.participation_group_id,
            Invitation.invitee_user_id == observation.invitee_user_id,
        )
        .with_for_update()
    )
    if invitation is not None:
        if observation.display_name and not invitation.invitee_display_name_snapshot:
            invitation.invitee_display_name_snapshot = observation.display_name
        return invitation
    invitation = Invitation(
        campaign_id=campaign.id,
        invite_link_id=link.id,
        inviter_participant_id=link.participant_id,
        participation_group_id=link.participation_group_id,
        invitee_user_id=observation.invitee_user_id,
        state=InvitationState.ATTRIBUTION_CANDIDATE,
        candidate_at=observation.event_at,
        invitee_display_name_snapshot=observation.display_name,
        source_update_id=current_update_id(),
    )
    session.add(invitation)
    await session.flush()
    return invitation


async def invalid_candidate(
    session: object,
    *,
    link: InviteLink,
    campaign: Campaign,
    observation: JoinObservation,
    reason: str,
) -> Invitation:
    invitation = await upsert_candidate(
        session, link=link, campaign=campaign, observation=observation
    )
    if invitation.state is InvitationState.VALID:
        return invitation
    invitation.state = InvitationState.INVALID
    invitation.validation_error = reason
    invitation.final_reason = reason
    return invitation


async def accepts_bonus(
    session: object,
    *,
    link: InviteLink,
    campaign: Campaign,
    event_at: datetime,
) -> bool:
    if not campaign_accepts_attribution(campaign, event_at):
        return False
    if link.referral_route_id is None:
        return True
    route = await session.get(CampaignReferralRoute, link.referral_route_id)
    return bool(
        route is not None and route.status == "active" and event_at <= route.bonus_join_deadline
    )


async def locked_invitation_by_user(
    session: object,
    campaign_id: UUID,
    invitee_user_id: int,
    *,
    participation_group_id: UUID | None,
) -> tuple[Invitation, Campaign]:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None:
        raise ValueError("invitation campaign is missing")
    filters = [
        Invitation.campaign_id == campaign_id,
        Invitation.invitee_user_id == invitee_user_id,
    ]
    if participation_group_id is not None:
        filters.append(Invitation.participation_group_id == participation_group_id)
    invitation = await session.scalar(select(Invitation).where(*filters).with_for_update())
    if invitation is None:
        raise ValueError("invitation not found")
    return invitation, campaign


async def record_existing_member(
    uow_factory: UnitOfWorkFactory,
    *,
    referral_route_id: UUID,
    invitee_user_id: int,
    event_at: datetime,
    display_name: str | None,
) -> Invitation | None:
    async with uow_factory() as uow:
        route = await uow.session.get(CampaignReferralRoute, referral_route_id)
        if route is None:
            raise ValueError("referral route not found")
        link = await uow.session.scalar(
            select(InviteLink).where(
                InviteLink.referral_route_id == route.id,
                InviteLink.is_current.is_(True),
            )
        )
        if link is None or await is_self_invite(uow.session, link, invitee_user_id):
            return None
        campaign = await uow.session.get(Campaign, route.campaign_id)
        if campaign is None:
            raise ValueError("referral campaign not found")
        await observe_existing_member(
            uow.session, link=link, user_id=invitee_user_id, observed_at=event_at
        )
        observation = JoinObservation(link.id, invitee_user_id, event_at, display_name=display_name)
        return await invalid_candidate(
            uow.session,
            link=link,
            campaign=campaign,
            observation=observation,
            reason="existing_member",
        )
