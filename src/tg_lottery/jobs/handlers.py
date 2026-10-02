from datetime import datetime
from uuid import UUID

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_notifications import add_campaign_card_state_fanout
from tg_lottery.application.draw_commitments import ensure_draw_commitment
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.full_draw import (
    FULL_DRAW_VERIFY_JOB,
    confirm_full_draw_candidate,
)
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.application.required_chat_links import add_required_chat_revoke_outboxes
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import (
    CampaignState,
    QualificationState,
)
from tg_lottery.domain.errors import (
    InvitationValidationDeferred,
    SettlementBlocked,
    UserFacingError,
)
from tg_lottery.domain.transitions import require_campaign_transition
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Group,
    InviteLink,
    ScheduledJob,
    TelegramOutbox,
)
from tg_lottery.jobs.errors import JobRetry
from tg_lottery.jobs.maintenance import (
    FULL_DRAW_RECONCILE_JOB_TYPE,
    TICKET_RECONCILE_JOB_TYPE,
    UPDATE_RETENTION_JOB_TYPE,
    next_full_draw_reconcile_at,
    next_maintenance_at,
    prune_processed_update_payloads,
    reconcile_full_draws,
    reconcile_ticket_caches,
    schedule_full_draw_reconcile,
    schedule_ticket_reconcile,
    schedule_update_retention,
)
from tg_lottery.jobs.qualification_review import QualificationReviewService
from tg_lottery.telegram.qualification import (
    QualificationRateLimited,
    QualificationRetryable,
    SmoothRateLimiter,
    observe_memberships,
)

_START_NOOP_STATES = frozenset(
    {
        CampaignState.SETTLING,
        CampaignState.READY_TO_DRAW,
        CampaignState.DRAWING,
        CampaignState.DRAWN,
        CampaignState.ARCHIVED,
        CampaignState.CANCELLED,
    }
)
_TERMINAL_STATES = frozenset({CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED})
_DEFAULT_QUALIFICATION_REQUESTS_PER_SECOND = 20.0


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
        message_stats: MessageStatsService | None = None,
        qualification_requests_per_second: float = _DEFAULT_QUALIFICATION_REQUESTS_PER_SECOND,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._invitations = invitations
        self._settlement = settlement
        self._drawing = drawing
        self._qualification_requests_per_second = qualification_requests_per_second
        self._qualification_review_service = QualificationReviewService(
            bot=bot,
            session_factory=session_factory,
            participants=participants,
            message_stats=message_stats,
            requests_per_second=qualification_requests_per_second,
        )

    async def handle(self, job: ScheduledJob) -> None:
        handlers = {
            "campaign_start": self._campaign_start,
            "campaign_cutoff": self._campaign_cutoff,
            "qualification_review": self._qualification_review,
            "validate_invitation": self._validate_invitation,
            "revoke_invite_links": self._revoke_invite_links,
            "campaign_settle": self._campaign_settle,
            "campaign_draw": self._campaign_draw,
            FULL_DRAW_VERIFY_JOB: self._full_draw_verify,
            UPDATE_RETENTION_JOB_TYPE: self._telegram_update_retention,
            TICKET_RECONCILE_JOB_TYPE: self._ticket_cache_reconcile,
            FULL_DRAW_RECONCILE_JOB_TYPE: self._full_draw_reconcile,
            "delete_telegram_message": self._delete_telegram_message,
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
            if campaign.state in _START_NOOP_STATES:
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
            await ensure_draw_commitment(session, campaign, utcnow())
            await add_campaign_card_state_fanout(session, campaign, state="settling")

    async def _qualification_review(self, job: ScheduledJob, *, force: bool = False) -> None:
        await self._qualification_review_service.run(job, force=force)

    async def _validate_invitation(self, job: ScheduledJob) -> None:
        invitation_id = UUID(job.aggregate_id)
        try:
            attempt = await self._invitations.begin_validation(invitation_id, observed_at=utcnow())
        except InvitationValidationDeferred as exc:
            raise JobRetry(str(exc)) from exc
        if not attempt.requires_observation or not attempt.chat_ids:
            return
        observed_at = utcnow()
        try:
            result = await observe_memberships(
                self._bot,
                chat_ids=attempt.chat_ids,
                user_id=attempt.invitee_user_id,
                observed_at=observed_at,
                limiter=SmoothRateLimiter(self._qualification_requests_per_second),
            )
        except QualificationRateLimited as exc:
            raise JobRetry(str(exc), delay_seconds=exc.retry_after) from exc
        except QualificationRetryable as exc:
            raise JobRetry(str(exc)) from exc
        try:
            await self._invitations.complete_validation(
                invitation_id,
                observed_at=observed_at,
                is_current_member=_membership_value(result.state),
                observations=result.observations,
            )
        except InvitationValidationDeferred as exc:
            raise JobRetry(str(exc)) from exc
        if result.state is QualificationState.UNKNOWN:
            raise JobRetry("one or more invitation conditions remain unknown")

    async def _revoke_invite_links(self, job: ScheduledJob) -> None:
        campaign_id = UUID(job.aggregate_id)
        async with self._session_factory() as session, session.begin():
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None:
                raise ValueError("campaign not found")
            group = await session.get(Group, campaign.group_id)
            if group is None:
                raise ValueError("campaign group not found")
            if job.payload.get("revoke_personal"):
                await self._queue_personal_link_revocations(
                    session, campaign_id=campaign_id, chat_id=group.chat_id
                )
            await add_required_chat_revoke_outboxes(session, campaign)

    async def _queue_personal_link_revocations(
        self, session: object, *, campaign_id: UUID, chat_id: int
    ) -> None:
        rows = (
            await session.execute(
                select(InviteLink, CampaignParticipationGroup.chat_id_snapshot)
                .outerjoin(
                    CampaignParticipationGroup,
                    CampaignParticipationGroup.id == InviteLink.participation_group_id,
                )
                .where(
                    InviteLink.campaign_id == campaign_id,
                    InviteLink.revoked_at.is_(None),
                )
            )
        ).all()
        for link, target_chat_id in rows:
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
                recipient_chat_id=target_chat_id or chat_id,
                business_key=business_key,
                payload={
                    "invite_link_id": str(link.id),
                    "reason": "campaign_cancelled",
                },
            )

    async def _campaign_settle(self, job: ScheduledJob) -> None:
        if await self._campaign_state(UUID(job.aggregate_id)) in _TERMINAL_STATES:
            return
        try:
            await self._settlement.settle(UUID(job.aggregate_id), utcnow())
        except SettlementBlocked as exc:
            raise JobRetry(str(exc)) from exc

    async def _campaign_draw(self, job: ScheduledJob) -> None:
        campaign_id = UUID(job.aggregate_id)
        state = await self._campaign_state(campaign_id)
        if state in _TERMINAL_STATES:
            return
        if state is not CampaignState.READY_TO_DRAW:
            raise JobRetry(f"campaign is not ready to draw: {state}")
        try:
            await self._drawing.run(campaign_id)
        except UserFacingError as exc:
            raise JobRetry(str(exc)) from exc

    async def _full_draw_verify(self, job: ScheduledJob) -> None:
        await self._qualification_review(job, force=True)
        candidate_at = datetime.fromisoformat(str(job.payload["candidate_at"]))
        try:
            async with self._session_factory() as session, session.begin():
                campaign = await self._locked_campaign(session, job.aggregate_id)
                await confirm_full_draw_candidate(
                    session, campaign=campaign, candidate_at=candidate_at
                )
        except SettlementBlocked as exc:
            raise JobRetry(str(exc)) from exc

    async def _telegram_update_retention(self, job: ScheduledJob) -> None:
        if job.aggregate_id != "system":
            raise ValueError("update retention job requires system aggregate")
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            await prune_processed_update_payloads(session, now)
            await schedule_update_retention(session, next_maintenance_at(now))

    async def _ticket_cache_reconcile(self, job: ScheduledJob) -> None:
        if job.aggregate_id != "system":
            raise ValueError("ticket reconcile job requires system aggregate")
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            await reconcile_ticket_caches(session)
            await schedule_ticket_reconcile(session, next_maintenance_at(now))

    async def _full_draw_reconcile(self, job: ScheduledJob) -> None:
        if job.aggregate_id != "system":
            raise ValueError("full draw reconcile job requires system aggregate")
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            await reconcile_full_draws(session, now)
            await schedule_full_draw_reconcile(session, next_full_draw_reconcile_at(now))

    async def _delete_telegram_message(self, job: ScheduledJob) -> None:
        chat_id = int(job.payload["chat_id"])
        message_id = int(job.payload["message_id"])
        business_key = f"delete_message:{chat_id}:{message_id}"
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
            )
            if existing:
                return
            add_outbox(
                session,
                operation="delete_message",
                aggregate_type="campaign",
                aggregate_id=job.aggregate_id,
                recipient_chat_id=chat_id,
                business_key=business_key,
                payload={"message_id": message_id},
            )

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


def _membership_value(state: QualificationState) -> bool | None:
    if state is QualificationState.ELIGIBLE:
        return True
    if state is QualificationState.INELIGIBLE:
        return False
    return None
