from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_job
from tg_lottery.domain.enums import CampaignState, InvitationState, TicketSource
from tg_lottery.domain.errors import InvitationValidationDeferred
from tg_lottery.domain.rules import CampaignRules, invite_due_at
from tg_lottery.infrastructure.db import (
    Campaign,
    Invitation,
    InviteeActivityEvent,
    InviteLink,
    Participant,
    TicketLedger,
)

_ATTRIBUTION_STATES = frozenset(
    {CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED}
)


@dataclass(frozen=True)
class JoinObservation:
    invite_link_id: UUID
    invitee_user_id: int
    event_at: datetime
    is_bot: bool = False
    was_already_member: bool = False


class InvitationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record_join_request(self, observation: JoinObservation) -> Invitation | None:
        if observation.is_bot or observation.was_already_member:
            return None
        async with self._uow_factory() as uow:
            link, campaign = await self._link_and_campaign(uow.session, observation.invite_link_id)
            if not self._accepts_attribution(campaign, observation.event_at):
                return None
            return await self._upsert_candidate(
                uow.session,
                link=link,
                campaign=campaign,
                observation=observation,
            )

    async def record_joined(self, observation: JoinObservation) -> Invitation | None:
        if observation.is_bot or observation.was_already_member:
            return None
        async with self._uow_factory() as uow:
            link, campaign = await self._link_and_campaign(uow.session, observation.invite_link_id)
            if not self._accepts_attribution(campaign, observation.event_at):
                return None
            invitation = await self._upsert_candidate(
                uow.session,
                link=link,
                campaign=campaign,
                observation=observation,
            )
            if invitation.state is InvitationState.VALID:
                return invitation
            rules = CampaignRules.model_validate(campaign.rules)
            due_at = invite_due_at(observation.event_at, rules.invite)
            if invitation.joined_at is not None:
                invitation.validation_version += 1
            invitation.joined_at = observation.event_at
            invitation.left_at = None
            invitation.due_at = due_at
            invitation.state = InvitationState.JOINED_PENDING
            add_job(
                uow.session,
                job_type="validate_invitation",
                aggregate_id=str(invitation.id),
                business_key=f"validate_invitation:{invitation.id}:{invitation.validation_version}",
                run_at=due_at,
            )
            return invitation

    async def record_left(
        self, campaign_id: UUID, invitee_user_id: int, event_at: datetime
    ) -> None:
        async with self._uow_factory() as uow:
            invitation = await uow.session.scalar(
                select(Invitation)
                .where(
                    Invitation.campaign_id == campaign_id,
                    Invitation.invitee_user_id == invitee_user_id,
                )
                .with_for_update()
            )
            if invitation is None or invitation.state is InvitationState.VALID:
                return
            invitation.left_at = event_at
            invitation.state = InvitationState.LEFT_BEFORE_VALID

    async def validate(
        self,
        invitation_id: UUID,
        *,
        observed_at: datetime,
        is_current_member: bool | None,
    ) -> Invitation:
        async with self._uow_factory() as uow:
            invitation = await uow.session.scalar(
                select(Invitation).where(Invitation.id == invitation_id).with_for_update()
            )
            if invitation is None:
                raise ValueError("invitation not found")
            campaign = await uow.session.scalar(
                select(Campaign).where(Campaign.id == invitation.campaign_id).with_for_update()
            )
            if campaign is None:
                raise ValueError("invitation campaign is missing")
            if invitation.state is InvitationState.VALID:
                return invitation
            return await self._apply_validation(
                uow.session,
                invitation=invitation,
                campaign=campaign,
                observed_at=observed_at,
                is_current_member=is_current_member,
            )

    async def _apply_validation(
        self,
        session: object,
        *,
        invitation: Invitation,
        campaign: Campaign,
        observed_at: datetime,
        is_current_member: bool | None,
    ) -> Invitation:
        invitation.state = InvitationState.CHECKING
        if campaign.state in {CampaignState.SCHEDULED, CampaignState.PAUSED}:
            raise InvitationValidationDeferred(
                f"campaign {campaign.state} is not accepting new tickets"
            )
        if campaign.state not in {CampaignState.RUNNING, CampaignState.SETTLING}:
            invitation.state = InvitationState.EXPIRED_UNQUALIFIED
            return invitation
        if is_current_member is None:
            invitation.state = InvitationState.VALIDATION_UNKNOWN
            invitation.validation_error = "membership_unknown"
            return invitation
        if not is_current_member:
            invitation.state = InvitationState.LEFT_BEFORE_VALID
            return invitation
        if invitation.due_at is None or invitation.due_at > campaign.cutoff_at:
            invitation.state = InvitationState.EXPIRED_UNQUALIFIED
            return invitation
        if observed_at < invitation.due_at:
            raise ValueError("invitation retention period has not elapsed")
        rules = CampaignRules.model_validate(campaign.rules)
        if not await self._activity_requirement_met(session, invitation, rules):
            invitation.state = InvitationState.INVALID
            invitation.validation_error = "invitee_activity_not_met"
            return invitation
        await self._award(
            session,
            invitation=invitation,
            campaign=campaign,
            rules=rules,
        )
        invitation.state = InvitationState.VALID
        invitation.validation_error = None
        return invitation

    async def _activity_requirement_met(
        self, session: object, invitation: Invitation, rules: CampaignRules
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

    async def _award(
        self,
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
        participant = await session.get(Participant, invitation.inviter_participant_id)
        if participant is None:
            raise ValueError("inviter participant is missing")
        reward = rules.invite.reward_tickets
        session.add(
            TicketLedger(
                campaign_id=campaign.id,
                participant_id=participant.id,
                delta=reward,
                source_type=TicketSource.INVITATION,
                source_ref=str(invitation.id),
                business_key=business_key,
                occurred_at=invitation.due_at,
            )
        )
        participant.ticket_total_cache += reward

    async def _link_and_campaign(
        self, session: object, invite_link_id: UUID
    ) -> tuple[InviteLink, Campaign]:
        link = await session.get(InviteLink, invite_link_id)
        if link is None:
            raise ValueError("invite link was not created by this bot")
        campaign = await session.scalar(
            select(Campaign).where(Campaign.id == link.campaign_id).with_for_update()
        )
        if campaign is None:
            raise ValueError("invite link campaign is missing")
        return link, campaign

    async def _upsert_candidate(
        self,
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
                Invitation.invitee_user_id == observation.invitee_user_id,
            )
            .with_for_update()
        )
        if invitation:
            return invitation
        invitation = Invitation(
            campaign_id=campaign.id,
            invite_link_id=link.id,
            inviter_participant_id=link.participant_id,
            invitee_user_id=observation.invitee_user_id,
            state=InvitationState.ATTRIBUTION_CANDIDATE,
            candidate_at=observation.event_at,
        )
        session.add(invitation)
        await session.flush()
        return invitation

    def _accepts_attribution(self, campaign: Campaign, event_at: datetime) -> bool:
        return campaign.state in _ATTRIBUTION_STATES and event_at <= campaign.cutoff_at
