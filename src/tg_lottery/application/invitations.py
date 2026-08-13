from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.invitation_models import (
    InvitationValidationAttempt,
    JoinObservation,
    accepts_attribution,
    required_chat_ids,
)
from tg_lottery.application.invitation_rewards import add_invite_bonus, invite_bonus_target
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


class InvitationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record_join_request(self, observation: JoinObservation) -> Invitation | None:
        if observation.is_bot or observation.was_already_member:
            return None
        async with self._uow_factory() as uow:
            link, campaign = await self._link_and_campaign(uow.session, observation.invite_link_id)
            if await self._is_self_invite(uow.session, link, observation.invitee_user_id):
                return None
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
            if await self._is_self_invite(uow.session, link, observation.invitee_user_id):
                return None
            if not self._accepts_attribution(campaign, observation.event_at):
                return None
            invitation = await self._upsert_candidate(
                uow.session,
                link=link,
                campaign=campaign,
                observation=observation,
            )
            if invitation.state is InvitationState.VALID and invitation.left_at is None:
                return invitation
            self._schedule_validation(
                uow.session, invitation, campaign, event_at=observation.event_at
            )
            return invitation

    async def record_rejoined(
        self, campaign_id: UUID, invitee_user_id: int, event_at: datetime
    ) -> Invitation | None:
        async with self._uow_factory() as uow:
            invitation, campaign = await self._locked_invitation_by_user(
                uow.session, campaign_id, invitee_user_id
            )
            recoverable = invitation.left_at is not None or (
                invitation.state is InvitationState.LEFT_BEFORE_VALID
            )
            if not recoverable or not self._accepts_attribution(campaign, event_at):
                return None
            self._schedule_validation(uow.session, invitation, campaign, event_at=event_at)
            return invitation

    async def record_left(
        self, campaign_id: UUID, invitee_user_id: int, event_at: datetime
    ) -> None:
        async with self._uow_factory() as uow:
            invitation, campaign = await self._locked_invitation_by_user(
                uow.session, campaign_id, invitee_user_id
            )
            if invitation.left_at is not None:
                return
            invitation.left_at = event_at
            if invitation.state is InvitationState.VALID:
                if event_at <= campaign.cutoff_at:
                    invitation.validation_version += 1
                    rules = CampaignRules.model_validate(campaign.rules)
                    await self._reconcile_tier_bonus(
                        uow.session,
                        invitation=invitation,
                        campaign=campaign,
                        rules=rules,
                        reason="left",
                        occurred_at=event_at,
                    )
                    invitation.state = InvitationState.INVALID
                    invitation.validation_error = "left_after_valid"
                return
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
                chat_ids=required_chat_ids(
                    group.chat_id, CampaignRules.model_validate(campaign.rules)
                ),
                requires_observation=True,
            )

    async def complete_validation(
        self,
        invitation_id: UUID,
        *,
        observed_at: datetime,
        is_current_member: bool | None,
        observations: tuple[dict[str, object], ...] = (),
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
                observations=observations,
            )

    async def _apply_validation(
        self,
        session: object,
        *,
        invitation: Invitation,
        campaign: Campaign,
        observed_at: datetime,
        is_current_member: bool | None,
        observations: tuple[dict[str, object], ...],
    ) -> Invitation:
        invitation.validation_observations = {
            "observed_at": observed_at.isoformat(),
            "chats": list(observations),
        }
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
            invitation.validation_error = "membership_ineligible"
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
        campaign_id = await session.scalar(
            select(Invitation.campaign_id).where(Invitation.id == invitation_id)
        )
        if campaign_id is None:
            raise ValueError("invitation not found")
        campaign = await session.scalar(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        )
        if campaign is None:
            raise ValueError("invitation campaign is missing")
        invitation = await session.scalar(
            select(Invitation).where(Invitation.id == invitation_id).with_for_update()
        )
        if invitation is None or invitation.campaign_id != campaign.id:
            raise ValueError("invitation not found")
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
            chat_ids=(),
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
        if not rules.invite_bonus_enabled:
            return
        if rules.invite.reward_tiers:
            await self._reconcile_tier_bonus(
                session,
                invitation=invitation,
                campaign=campaign,
                rules=rules,
                reason="valid",
                occurred_at=invitation.due_at or invitation.joined_at,
            )
            return
        await self._award_legacy_bonus(
            session,
            invitation=invitation,
            campaign=campaign,
            rules=rules,
        )

    async def _reconcile_tier_bonus(
        self,
        session: object,
        *,
        invitation: Invitation,
        campaign: Campaign,
        rules: CampaignRules,
        reason: str,
        occurred_at: datetime | None,
    ) -> None:
        valid_count = await session.scalar(
            select(func.count(Invitation.id)).where(
                Invitation.campaign_id == campaign.id,
                Invitation.inviter_participant_id == invitation.inviter_participant_id,
                Invitation.id != invitation.id,
                Invitation.state == InvitationState.VALID,
                Invitation.left_at.is_(None),
            )
        )
        invite_count = int(valid_count or 0)
        if reason == "valid":
            invite_count += 1
        target = invite_bonus_target(rules, invite_count)
        current = await session.scalar(
            select(func.coalesce(func.sum(TicketLedger.delta), 0)).where(
                TicketLedger.campaign_id == campaign.id,
                TicketLedger.participant_id == invitation.inviter_participant_id,
                TicketLedger.source_type.in_([TicketSource.INVITATION, TicketSource.REVERSAL]),
            )
        )
        delta = target - int(current or 0)
        if delta == 0:
            return
        await add_invite_bonus(
            session,
            invitation=invitation,
            campaign=campaign,
            reward=delta,
            business_key=(
                f"invite_bonus:{campaign.id}:{invitation.id}:"
                f"{invitation.validation_version}:{reason}"
            ),
            occurred_at=occurred_at,
        )

    async def _award_legacy_bonus(
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
        await add_invite_bonus(
            session,
            invitation=invitation,
            campaign=campaign,
            reward=rules.invite.reward_tickets,
            business_key=business_key,
            occurred_at=invitation.due_at,
        )

    def _schedule_validation(
        self,
        session: object,
        invitation: Invitation,
        campaign: Campaign,
        *,
        event_at: datetime,
    ) -> None:
        rules = CampaignRules.model_validate(campaign.rules)
        due_at = invite_due_at(event_at, rules.invite)
        if invitation.joined_at is not None:
            invitation.validation_version += 1
        invitation.joined_at = event_at
        invitation.left_at = None
        invitation.due_at = due_at
        invitation.state = InvitationState.JOINED_PENDING
        invitation.source_update_id = current_update_id()
        add_job(
            session,
            job_type="validate_invitation",
            aggregate_id=str(invitation.id),
            business_key=f"validate_invitation:{invitation.id}:{invitation.validation_version}",
            run_at=due_at,
        )

    async def _locked_invitation_by_user(
        self,
        session: object,
        campaign_id: UUID,
        invitee_user_id: int,
    ) -> tuple[Invitation, Campaign]:
        campaign = await session.scalar(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        )
        if campaign is None:
            raise ValueError("invitation campaign is missing")
        invitation = await session.scalar(
            select(Invitation)
            .where(
                Invitation.campaign_id == campaign_id,
                Invitation.invitee_user_id == invitee_user_id,
            )
            .with_for_update()
        )
        if invitation is None:
            raise ValueError("invitation not found")
        return invitation, campaign

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

    async def _is_self_invite(
        self, session: object, link: InviteLink, invitee_user_id: int
    ) -> bool:
        inviter_user_id = await session.scalar(
            select(Participant.user_id).where(Participant.id == link.participant_id)
        )
        if inviter_user_id is None:
            raise ValueError("invite link participant is missing")
        return inviter_user_id == invitee_user_id

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
        return accepts_attribution(
            campaign.state,
            preheat_at=campaign.preheat_at,
            cutoff_at=campaign.cutoff_at,
            event_at=event_at,
        )
