from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.admission_invites import update_invite_admission
from tg_lottery.application.group_membership_facts import (
    MembershipObservation,
    first_source_matches_invitation,
    has_membership_history,
    mark_invitation_member_left,
    observe_existing_member,
    observe_join,
)
from tg_lottery.application.invitation_bonus_service import (
    award_invitation_bonus,
    invitation_activity_requirement_met,
)
from tg_lottery.application.invitation_models import (
    InvitationValidationAttempt,
    JoinObservation,
    campaign_accepts_attribution,
)
from tg_lottery.application.invitation_records import (
    acceptance_reason,
    invalid_candidate,
    is_self_invite,
    link_and_campaign,
    locked_invitation_by_user,
    matches_target_group,
    participation_target,
    upsert_candidate,
)
from tg_lottery.application.invitation_records import (
    record_existing_member as record_existing_member_fact,
)
from tg_lottery.application.invitation_validation import (
    mark_terminal_validation,
    validation_chat_ids,
)
from tg_lottery.application.operational_blocks import (
    has_active_campaign_block,
    has_active_target_block,
)
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.progress_notifications import flush_progress_notices
from tg_lottery.application.records import add_job
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import CampaignState, InvitationState
from tg_lottery.domain.errors import InvitationValidationDeferred
from tg_lottery.domain.rules import CampaignRules, invite_due_at
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Group,
    Invitation,
    InviteLink,
    Participant,
)


class InvitationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record_join_request(self, observation: JoinObservation) -> Invitation | None:
        if observation.is_bot:
            return None
        async with self._uow_factory() as uow:
            link, campaign = await link_and_campaign(uow.session, observation.invite_link_id)
            if not await matches_target_group(uow.session, link, observation.chat_id):
                return None
            if await is_self_invite(uow.session, link, observation.invitee_user_id):
                return None
            if _ignore_join_observation(link, campaign, observation.event_at):
                return None
            if observation.was_already_member:
                await observe_existing_member(
                    uow.session,
                    link=link,
                    user_id=observation.invitee_user_id,
                    observed_at=observation.event_at,
                )
            if observation.was_already_member or await has_membership_history(
                uow.session, link=link, user_id=observation.invitee_user_id
            ):
                return await invalid_candidate(
                    uow.session,
                    link=link,
                    campaign=campaign,
                    observation=observation,
                    reason="existing_member",
                )
            reason = await acceptance_reason(
                uow.session, link=link, campaign=campaign, event_at=observation.event_at
            )
            if reason:
                return await invalid_candidate(
                    uow.session,
                    link=link,
                    campaign=campaign,
                    observation=observation,
                    reason=reason,
                )
            return await upsert_candidate(
                uow.session,
                link=link,
                campaign=campaign,
                observation=observation,
            )

    async def record_joined(self, observation: JoinObservation) -> Invitation | None:
        if observation.is_bot:
            return None
        async with self._uow_factory() as uow:
            link, campaign = await link_and_campaign(uow.session, observation.invite_link_id)
            return await self._record_joined_fact(
                uow.session, link, campaign, observation=observation
            )

    async def _record_joined_fact(
        self,
        session: object,
        link: InviteLink,
        campaign: Campaign,
        *,
        observation: JoinObservation,
    ) -> Invitation | None:
        if not await matches_target_group(session, link, observation.chat_id):
            return None
        if await is_self_invite(session, link, observation.invitee_user_id):
            return None
        membership = await _observe_join_membership(session, link, observation)
        if _ignore_join_observation(link, campaign, observation.event_at):
            return None
        if observation.was_already_member or not membership.first_for_link:
            return await invalid_candidate(
                session,
                link=link,
                campaign=campaign,
                observation=observation,
                reason="existing_member",
            )
        reason = await acceptance_reason(
            session, link=link, campaign=campaign, event_at=observation.event_at
        )
        if reason:
            return await invalid_candidate(
                session,
                link=link,
                campaign=campaign,
                observation=observation,
                reason=reason,
            )
        invitation = await upsert_candidate(
            session, link=link, campaign=campaign, observation=observation
        )
        if invitation.state is InvitationState.VALID:
            return invitation
        self._schedule_validation(session, invitation, campaign, event_at=observation.event_at)
        return invitation

    async def record_rejoined(
        self,
        campaign_id: UUID,
        invitee_user_id: int,
        event_at: datetime,
        *,
        participation_group_id: UUID | None = None,
    ) -> Invitation | None:
        async with self._uow_factory() as uow:
            invitation, campaign = await locked_invitation_by_user(
                uow.session,
                campaign_id,
                invitee_user_id,
                participation_group_id=participation_group_id,
            )
            if invitation.state is InvitationState.VALID:
                return invitation
            recoverable = invitation.left_at is not None or (
                invitation.state is InvitationState.LEFT_BEFORE_VALID
            )
            if not recoverable or not campaign_accepts_attribution(campaign, event_at):
                return None
            if not await first_source_matches_invitation(uow.session, invitation):
                return None
            self._schedule_validation(uow.session, invitation, campaign, event_at=event_at)
            return invitation

    async def record_left(
        self,
        campaign_id: UUID,
        invitee_user_id: int,
        event_at: datetime,
        *,
        participation_group_id: UUID | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            invitation, _ = await locked_invitation_by_user(
                uow.session,
                campaign_id,
                invitee_user_id,
                participation_group_id=participation_group_id,
            )
            if invitation.left_at is not None:
                return
            invitation.left_at = event_at
            await mark_invitation_member_left(uow.session, invitation, observed_at=event_at)
            if invitation.state is InvitationState.VALID:
                return
            invitation.state = InvitationState.LEFT_BEFORE_VALID
            invitation.final_reason = "left_before_valid"

    async def record_existing_member(
        self,
        referral_route_id: UUID,
        invitee_user_id: int,
        event_at: datetime,
        *,
        display_name: str | None = None,
    ) -> Invitation | None:
        return await record_existing_member_fact(
            self._uow_factory,
            referral_route_id=referral_route_id,
            invitee_user_id=invitee_user_id,
            event_at=event_at,
            display_name=display_name,
        )

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
            target = await participation_target(uow.session, invitation)
            if await self._validation_blocked(uow.session, campaign.id, target=target):
                raise InvitationValidationDeferred("campaign operational block is active")
            self._require_validation_window(invitation, campaign, observed_at)
            if mark_terminal_validation(invitation, campaign):
                return self._no_observation(invitation)
            chat_ids = await validation_chat_ids(
                uow.session,
                campaign=campaign,
                target=target,
            )
            invitation.state = InvitationState.CHECKING
            invitation.validation_error = None
            return InvitationValidationAttempt(
                invitation_id=invitation.id,
                invitee_user_id=invitation.invitee_user_id,
                chat_ids=chat_ids,
                requires_observation=True,
            )

    async def _validation_blocked(
        self,
        session: object,
        campaign_id: UUID,
        *,
        target: CampaignParticipationGroup | None,
    ) -> bool:
        if target is None:
            return await has_active_campaign_block(session, campaign_id)
        group = await session.get(Group, target.group_id)
        chat_ids = {target.chat_id_snapshot}
        if group is not None:
            chat_ids.add(group.chat_id)
        return await has_active_target_block(session, campaign_id, chat_ids=tuple(chat_ids))

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
            invitation.final_reason = "campaign_closed"
            return invitation
        if is_current_member is None:
            invitation.state = InvitationState.VALIDATION_UNKNOWN
            invitation.validation_error = "membership_unknown"
            invitation.final_reason = "validation_unknown"
            return invitation
        if not is_current_member:
            invitation.state = InvitationState.LEFT_BEFORE_VALID
            invitation.validation_error = "membership_ineligible"
            invitation.final_reason = "left_before_valid"
            return invitation
        rules = CampaignRules.model_validate(campaign.rules)
        if not await invitation_activity_requirement_met(session, invitation, rules):
            invitation.state = InvitationState.INVALID
            invitation.validation_error = "invitee_activity_not_met"
            invitation.final_reason = "invitee_activity_not_met"
            return invitation
        return await _confirm_valid_invitation(
            session,
            invitation=invitation,
            campaign=campaign,
            rules=rules,
        )

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
        invitation.final_reason = None
        invitation.source_update_id = current_update_id()
        add_job(
            session,
            job_type="validate_invitation",
            aggregate_id=str(invitation.id),
            business_key=f"validate_invitation:{invitation.id}:{invitation.validation_version}",
            run_at=due_at,
        )


def _ignore_join_observation(link: InviteLink, campaign: Campaign, event_at: datetime) -> bool:
    if event_at < campaign.preheat_at:
        return True
    return link.referral_route_id is None and not campaign_accepts_attribution(campaign, event_at)


async def _observe_join_membership(
    session: object, link: InviteLink, observation: JoinObservation
) -> MembershipObservation:
    if observation.was_already_member:
        await observe_existing_member(
            session,
            link=link,
            user_id=observation.invitee_user_id,
            observed_at=observation.event_at,
        )
    return await observe_join(
        session,
        link=link,
        user_id=observation.invitee_user_id,
        observed_at=observation.event_at,
    )


async def _confirm_valid_invitation(session: object, *, invitation, campaign, rules):
    await award_invitation_bonus(
        session,
        invitation=invitation,
        campaign=campaign,
        rules=rules,
    )
    invitation.state = InvitationState.VALID
    await session.flush()
    await update_invite_admission(session, campaign=campaign, invitation=invitation)
    participant = await session.get(Participant, invitation.inviter_participant_id)
    if participant is None:
        raise ValueError("inviter participant is missing")
    await flush_progress_notices(
        session,
        campaign=campaign,
        participant=participant,
        source_ref=f"invitation:{invitation.id}:{invitation.validation_version}",
        event_at=invitation.due_at or invitation.joined_at,
    )
    invitation.validation_error = None
    invitation.final_reason = None
    return invitation
