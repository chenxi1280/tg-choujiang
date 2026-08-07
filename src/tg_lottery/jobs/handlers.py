from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    QualificationState,
)
from tg_lottery.domain.errors import InvitationValidationDeferred, SettlementBlocked
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.domain.transitions import require_campaign_transition
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    Invitation,
    InviteLink,
    Participant,
    QualificationCheck,
    ScheduledJob,
    TelegramOutbox,
)
from tg_lottery.jobs.errors import JobRetry


class JobHandlers:
    def __init__(
        self,
        *,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        participants: ParticipantService,
        invitations: InvitationService,
        settlement: SettlementService,
        drawing: DrawingService,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._participants = participants
        self._invitations = invitations
        self._settlement = settlement
        self._drawing = drawing

    async def handle(self, job: ScheduledJob) -> None:
        handlers = {
            "campaign_start": self._campaign_start,
            "campaign_cutoff": self._campaign_cutoff,
            "qualification_review": self._qualification_review,
            "validate_invitation": self._validate_invitation,
            "revoke_invite_links": self._revoke_invite_links,
            "campaign_settle": self._campaign_settle,
            "campaign_draw": self._campaign_draw,
        }
        handler = handlers.get(job.job_type)
        if handler is None:
            raise ValueError(f"unknown scheduled job type: {job.job_type}")
        await handler(job)

    async def _campaign_start(self, job: ScheduledJob) -> None:
        async with self._session_factory() as session, session.begin():
            campaign = await self._locked_campaign(session, job.aggregate_id)
            if campaign.state is CampaignState.RUNNING:
                return
            if campaign.state is not CampaignState.SCHEDULED:
                raise JobRetry(f"campaign cannot start from {campaign.state}")
            require_campaign_transition(campaign.state, CampaignState.RUNNING)
            campaign.state = CampaignState.RUNNING

    async def _campaign_cutoff(self, job: ScheduledJob) -> None:
        async with self._session_factory() as session, session.begin():
            campaign = await self._locked_campaign(session, job.aggregate_id)
            if campaign.state is CampaignState.SETTLING:
                return
            if campaign.state in {
                CampaignState.CANCELLED,
                CampaignState.READY_TO_DRAW,
                CampaignState.DRAWING,
                CampaignState.DRAWN,
                CampaignState.ARCHIVED,
            }:
                return
            if campaign.state not in {
                CampaignState.SCHEDULED,
                CampaignState.RUNNING,
                CampaignState.PAUSED,
            }:
                raise JobRetry(f"campaign cannot settle from {campaign.state}")
            require_campaign_transition(campaign.state, CampaignState.SETTLING)
            campaign.state = CampaignState.SETTLING

    async def _qualification_review(self, job: ScheduledJob) -> None:
        campaign_id = UUID(job.aggregate_id)
        campaign, participants = await self._campaign_and_participants(campaign_id)
        rules = CampaignRules.model_validate(campaign.rules)
        unresolved = False
        for participant in participants:
            if await self._has_final_draw_check(participant.id):
                continue
            state, status, error = await self._observe_channel(
                rules.required_channel_id, participant.user_id
            )
            await self._participants.record_draw_qualification(
                participant_id=participant.id,
                state=state,
                observed_at=utcnow(),
                observed_status=status,
                error_code=error,
            )
            unresolved = unresolved or state is QualificationState.UNKNOWN
        if unresolved:
            raise JobRetry("one or more channel qualifications remain unknown")

    async def _validate_invitation(self, job: ScheduledJob) -> None:
        invitation_id = UUID(job.aggregate_id)
        invitation, chat_id = await self._invitation_and_chat(invitation_id)
        if invitation.state is InvitationState.VALID:
            return
        try:
            member = await self._bot.get_chat_member(chat_id, invitation.invitee_user_id)
            is_member = _is_member(member)
        except TelegramAPIError as exc:
            await self._invitations.validate(
                invitation_id,
                observed_at=utcnow(),
                is_current_member=None,
            )
            raise JobRetry(f"membership observation failed: {exc}") from exc
        try:
            await self._invitations.validate(
                invitation_id,
                observed_at=utcnow(),
                is_current_member=is_member,
            )
        except InvitationValidationDeferred as exc:
            raise JobRetry(str(exc)) from exc

    async def _revoke_invite_links(self, job: ScheduledJob) -> None:
        campaign_id = UUID(job.aggregate_id)
        async with self._session_factory() as session, session.begin():
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None:
                raise ValueError("campaign not found")
            group = await session.get(Group, campaign.group_id)
            if group is None:
                raise ValueError("campaign group not found")
            links = (
                await session.scalars(
                    select(InviteLink).where(
                        InviteLink.campaign_id == campaign_id,
                        InviteLink.revoked_at.is_(None),
                    )
                )
            ).all()
            for link in links:
                business_key = f"revoke_invite_link:{link.id}"
                exists = await session.scalar(
                    select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
                )
                if exists:
                    continue
                add_outbox(
                    session,
                    operation="revoke_invite_link",
                    aggregate_type="invite_link",
                    aggregate_id=str(link.id),
                    recipient_chat_id=group.chat_id,
                    business_key=business_key,
                    payload={"invite_link_id": str(link.id)},
                )

    async def _campaign_settle(self, job: ScheduledJob) -> None:
        try:
            await self._settlement.settle(UUID(job.aggregate_id), utcnow())
        except SettlementBlocked as exc:
            raise JobRetry(str(exc)) from exc

    async def _campaign_draw(self, job: ScheduledJob) -> None:
        campaign_id = UUID(job.aggregate_id)
        state = await self._campaign_state(campaign_id)
        if state is CampaignState.DRAWN:
            return
        if state is not CampaignState.READY_TO_DRAW:
            raise JobRetry(f"campaign is not ready to draw: {state}")
        await self._drawing.run(campaign_id)

    async def _campaign_and_participants(
        self, campaign_id: UUID
    ) -> tuple[Campaign, list[Participant]]:
        async with self._session_factory() as session:
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None:
                raise ValueError("campaign not found")
            participants = (
                await session.scalars(
                    select(Participant).where(Participant.campaign_id == campaign_id)
                )
            ).all()
            return campaign, list(participants)

    async def _observe_channel(
        self, channel_id: int | None, user_id: int
    ) -> tuple[QualificationState, str, str | None]:
        if channel_id is None:
            return QualificationState.ELIGIBLE, "not_required", None
        try:
            member = await self._bot.get_chat_member(channel_id, user_id)
        except TelegramAPIError as exc:
            return QualificationState.UNKNOWN, "unavailable", type(exc).__name__
        status = str(member.status)
        state = QualificationState.ELIGIBLE if _is_member(member) else QualificationState.INELIGIBLE
        return state, status, None

    async def _has_final_draw_check(self, participant_id: UUID) -> bool:
        async with self._session_factory() as session:
            state = await session.scalar(
                select(QualificationCheck.state)
                .where(
                    QualificationCheck.participant_id == participant_id,
                    QualificationCheck.phase == "draw",
                )
                .order_by(QualificationCheck.observed_at.desc())
                .limit(1)
            )
            return state in {QualificationState.ELIGIBLE, QualificationState.INELIGIBLE}

    async def _invitation_and_chat(self, invitation_id: UUID) -> tuple[Invitation, int]:
        async with self._session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            if invitation is None:
                raise ValueError("invitation not found")
            if invitation.state is InvitationState.VALID:
                return invitation, 0
            campaign = await session.get(Campaign, invitation.campaign_id)
            group = await session.get(Group, campaign.group_id) if campaign else None
            if group is None:
                raise ValueError("invitation group not found")
            return invitation, group.chat_id

    async def _campaign_state(self, campaign_id: UUID) -> CampaignState:
        async with self._session_factory() as session:
            state = await session.scalar(select(Campaign.state).where(Campaign.id == campaign_id))
            if state is None:
                raise ValueError("campaign not found")
            return state

    async def _locked_campaign(self, session: AsyncSession, aggregate_id: str) -> Campaign:
        campaign = await session.get(Campaign, UUID(aggregate_id), with_for_update=True)
        if campaign is None:
            raise ValueError("campaign not found")
        return campaign


def _is_member(member: object) -> bool:
    status = str(getattr(member, "status", ""))
    if status in {"member", "administrator", "creator"}:
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))
