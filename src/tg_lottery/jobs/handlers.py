from datetime import datetime
from typing import Any
from uuid import UUID

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_notifications import add_campaign_card_state_fanout
from tg_lottery.application.campaign_targets import QualificationTarget, qualification_targets
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
from tg_lottery.domain.errors import InvitationValidationDeferred, SettlementBlocked
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.domain.transitions import require_campaign_transition
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    InviteLink,
    Participant,
    QualificationCheck,
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
from tg_lottery.telegram.qualification import (
    QualificationObservationResult,
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
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._participants = participants
        self._invitations = invitations
        self._settlement = settlement
        self._drawing = drawing
        self._message_stats = message_stats

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
            await add_campaign_card_state_fanout(session, campaign, state="settling")

    async def _qualification_review(self, job: ScheduledJob, *, force: bool = False) -> None:
        campaign_id = UUID(job.aggregate_id)
        campaign, targets, participants = await self._campaign_and_participants(campaign_id)
        if campaign.state in _TERMINAL_STATES:
            return
        rules = CampaignRules.model_validate(campaign.rules)
        chat_ids = await _target_chat_ids(self._session_factory, campaign, rules, targets=targets)
        unresolved = False
        for participant in participants:
            if not force and await self._has_final_draw_check(participant.id):
                continue
            observed_at = utcnow()
            result = await observe_memberships(
                self._bot,
                chat_ids=chat_ids,
                user_id=participant.user_id,
                observed_at=observed_at,
            )
            state, observations = await self._merge_message_thresholds(
                user_id=participant.user_id,
                rules=rules,
                result=result,
                observed_at=observed_at,
            )
            observations = _tag_target_kinds(observations, targets)
            await self._participants.record_draw_qualification(
                participant_id=participant.id,
                state=state,
                observed_at=observed_at,
                observed_status=result.observed_status,
                error_code=result.error_code,
                observations=observations,
            )
            unresolved = unresolved or result.state is QualificationState.UNKNOWN
        if unresolved:
            raise JobRetry("one or more channel qualifications remain unknown")

    async def _merge_message_thresholds(
        self,
        *,
        user_id: int,
        rules: CampaignRules,
        result: QualificationObservationResult,
        observed_at: datetime,
    ) -> tuple[QualificationState, tuple[dict[str, Any], ...]]:
        thresholds = {
            int(key): int(value) for key, value in (rules.message_thresholds or {}).items()
        }
        state = result.state
        observations = tuple(result.observations)
        if not thresholds:
            return state, observations
        member_ok = {
            int(item["chat_id"])
            for item in observations
            if item["state"] == QualificationState.ELIGIBLE.value
        }
        totals = await self._message_stats.totals(user_id, tuple(thresholds))
        shortfalls = tuple(
            {
                "chat_id": chat_id,
                "state": "ineligible",
                "observed_status": f"messages {totals.get(chat_id, 0)}/{need}",
                "error_code": "message_threshold",
                "observed_at": observed_at.isoformat(),
            }
            for chat_id, need in thresholds.items()
            if chat_id in member_ok and totals.get(chat_id, 0) < need
        )
        if shortfalls:
            state = QualificationState.INELIGIBLE
            observations = observations + shortfalls
        return state, observations

    async def _validate_invitation(self, job: ScheduledJob) -> None:
        invitation_id = UUID(job.aggregate_id)
        try:
            attempt = await self._invitations.begin_validation(invitation_id, observed_at=utcnow())
        except InvitationValidationDeferred as exc:
            raise JobRetry(str(exc)) from exc
        if not attempt.requires_observation or not attempt.chat_ids:
            return
        observed_at = utcnow()
        result = await observe_memberships(
            self._bot,
            chat_ids=attempt.chat_ids,
            user_id=attempt.invitee_user_id,
            observed_at=observed_at,
        )
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
                recipient_chat_id=chat_id,
                business_key=business_key,
                payload={"invite_link_id": str(link.id)},
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
        await self._drawing.run(campaign_id)

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

    async def _campaign_and_participants(
        self, campaign_id: UUID
    ) -> tuple[Campaign, tuple[QualificationTarget, ...], list[Participant]]:
        async with self._session_factory() as session:
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None:
                raise ValueError("campaign not found")
            targets = await qualification_targets(session, campaign)
            participants = (
                await session.scalars(
                    select(Participant).where(Participant.campaign_id == campaign_id)
                )
            ).all()
            return campaign, targets, list(participants)

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


def _required_chat_ids(main_chat_id: int, rules: CampaignRules) -> tuple[int, ...]:
    values = [main_chat_id]
    if rules.required_channel_id is not None:
        values.append(rules.required_channel_id)
    values.extend(rules.required_chat_ids)
    return tuple(dict.fromkeys(values))


async def _target_chat_ids(
    session_factory: async_sessionmaker[AsyncSession],
    campaign: Campaign,
    rules: CampaignRules,
    *,
    targets: tuple[QualificationTarget, ...],
) -> tuple[int, ...]:
    if targets:
        return tuple(target.chat_id for target in targets)
    main = campaign.presentation.get("main_chat") or {}
    if main.get("id") is not None:
        return _required_chat_ids(int(main["id"]), rules)
    async with session_factory() as session:
        group = await session.get(Group, campaign.group_id)
    if group is None:
        raise ValueError("campaign group not found")
    return _required_chat_ids(group.chat_id, rules)


def _tag_target_kinds(
    observations: tuple[dict[str, Any], ...],
    targets: tuple[QualificationTarget, ...],
) -> tuple[dict[str, Any], ...]:
    kinds = {target.chat_id: target.kind for target in targets}
    return tuple(
        dict(item) | {"target_kind": kinds.get(int(item["chat_id"]), "requirement")}
        for item in observations
    )
