from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_job
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import CampaignState, InvitationState, TicketSource
from tg_lottery.domain.errors import InvitationValidationDeferred
from tg_lottery.domain.rules import CampaignRules, invite_due_at
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
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


@dataclass(frozen=True)
class InvitationValidationAttempt:
    invitation_id: UUID
    invitee_user_id: int
    chat_id: int | None
    requires_observation: bool


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
            invitation.source_update_id = current_update_id()
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
        attempt = await self.begin_validation(invitation_id, observed_at=observed_at)
        if not attempt.requires_observation:
            return await self._current(invitation_id)
        return await self.complete_validation(
            invitation_id,
            observed_at=observed_at,
            is_current_member=is_current_member,
        )

    async def begin_validation(
        self, invitation_id: UUID, *, observed_at: datetime
    ) -> InvitationValidationAttempt:
        async with self._uow_factory() as uow:
            invitation, campaign = await self._locked_invitation_campaign(
                uow.session, invitation_id
            )
            if invitation.state is InvitationState.VALID:
                return self._no_observation(invitation)
            self._require_validation_window(invitation, campaign, observed_at)
            if invitation.due_at is None or invitation.due_at > campaign.cutoff_at:
                invitation.state = InvitationState.EXPIRED_UNQUALIFIED
                return self._no_observation(invitation)
            if campaign.state not in {CampaignState.RUNNING, CampaignState.SETTLING}:
                invitation.state = InvitationState.EXPIRED_UNQUALIFIED
                return self._no_observation(invitation)
            if invitation.state in {
                InvitationState.LEFT_BEFORE_VALID,
                InvitationState.INVALID,
                InvitationState.EXPIRED_UNQUALIFIED,
            }:
                return self._no_observation(invitation)
            group = await uow.session.get(Group, campaign.group_id)
            if group is None:
                raise ValueError("invitation group is missing")
            invitation.state = InvitationState.CHECKING
            invitation.validation_error = None
            return InvitationValidationAttempt(
                invitation_id=invitation.id,
                invitee_user_id=invitation.invitee_user_id,
                chat_id=group.chat_id,
                requires_observation=True,
            )

    async def complete_validation(
        self,
        invitation_id: UUID,
        *,
        observed_at: datetime,
        is_current_member: bool | None,
    ) -> Invitation:
        async with self._uow_factory() as uow:
            invitation, campaign = await self._locked_invitation_campaign(
                uow.session, invitation_id
            )
            if invitation.state is InvitationState.VALID:
                return invitation
            if invitation.state is not InvitationState.CHECKING:
                raise ValueError("invitation validation was not started")
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

    async def _locked_invitation_campaign(
        self, session: object, invitation_id: UUID
    ) -> tuple[Invitation, Campaign]:
        invitation = await session.scalar(
            select(Invitation).where(Invitation.id == invitation_id).with_for_update()
        )
        if invitation is None:
            raise ValueError("invitation not found")
        campaign = await session.scalar(
            select(Campaign).where(Campaign.id == invitation.campaign_id).with_for_update()
        )
        if campaign is None:
            raise ValueError("invitation campaign is missing")
        return invitation, campaign

    async def _current(self, invitation_id: UUID) -> Invitation:
        async with self._uow_factory() as uow:
            invitation = await uow.session.get(Invitation, invitation_id)
            if invitation is None:
                raise ValueError("invitation not found")
            return invitation

    def _require_validation_window(
        self, invitation: Invitation, campaign: Campaign, observed_at: datetime
    ) -> None:
        if campaign.state in {CampaignState.SCHEDULED, CampaignState.PAUSED}:
            raise InvitationValidationDeferred(
                f"campaign {campaign.state} is not accepting new tickets"
            )
        if invitation.due_at is not None and observed_at < invitation.due_at:
            raise ValueError("invitation retention period has not elapsed")

    def _no_observation(self, invitation: Invitation) -> InvitationValidationAttempt:
        return InvitationValidationAttempt(
            invitation_id=invitation.id,
            invitee_user_id=invitation.invitee_user_id,
            chat_id=None,
            requires_observation=False,
        )

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
                source_update_id=invitation.source_update_id,
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
            source_update_id=current_update_id(),
        )
        session.add(invitation)
        await session.flush()
        return invitation

    def _accepts_attribution(self, campaign: Campaign, event_at: datetime) -> bool:
        return campaign.state in _ATTRIBUTION_STATES and event_at <= campaign.cutoff_at
