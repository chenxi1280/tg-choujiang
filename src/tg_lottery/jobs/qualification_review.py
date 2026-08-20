from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from aiogram import Bot
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_targets import QualificationTarget, qualification_targets
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.application.operational_blocks import (
    record_qualification_target_failure,
    resolve_qualification_target_blocks,
)
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    Participant,
    QualificationCheck,
    ScheduledJob,
)
from tg_lottery.jobs.errors import JobRetry
from tg_lottery.telegram.qualification import (
    QualificationObservationResult,
    QualificationRateLimited,
    QualificationRetryable,
    SmoothRateLimiter,
    TargetPreflightFailure,
    observe_memberships,
    preflight_membership_targets,
)

_TERMINAL_STATES = frozenset({CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED})


class QualificationReviewService:
    def __init__(
        self,
        *,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        participants: ParticipantService,
        message_stats: MessageStatsService | None,
        requests_per_second: float,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._participants = participants
        self._message_stats = message_stats
        self._requests_per_second = requests_per_second

    async def run(self, job: ScheduledJob, *, force: bool = False) -> None:
        campaign_id = UUID(job.aggregate_id)
        campaign, targets, participants = await self._context(campaign_id, force=force)
        if campaign.state in _TERMINAL_STATES:
            return
        rules = CampaignRules.model_validate(campaign.rules)
        chat_ids = await self._target_chat_ids(campaign, rules, targets=targets)
        limiter = SmoothRateLimiter(self._requests_per_second)
        try:
            failures = await self._preflight(
                campaign_id, targets=targets, chat_ids=chat_ids, limiter=limiter
            )
            if failures:
                await self._degrade_review(campaign_id, participants, failures=failures)
                return
            await self._review_participants(
                participants,
                targets=targets,
                rules=rules,
                chat_ids=chat_ids,
                limiter=limiter,
                attempt_number=job.attempts,
            )
        except QualificationRateLimited as exc:
            raise JobRetry(str(exc), delay_seconds=exc.retry_after) from exc
        except QualificationRetryable as exc:
            raise JobRetry(str(exc)) from exc

    async def _preflight(
        self,
        campaign_id: UUID,
        *,
        targets: tuple[QualificationTarget, ...],
        chat_ids: tuple[int, ...],
        limiter: SmoothRateLimiter,
    ) -> tuple[TargetPreflightFailure, ...]:
        failures = await preflight_membership_targets(self._bot, chat_ids=chat_ids, limiter=limiter)
        if failures:
            failed_ids = {failure.chat_id for failure in failures}
            recovered = tuple(chat_id for chat_id in chat_ids if chat_id not in failed_ids)
            await self._resolve_blocks(campaign_id, recovered)
            exhausted = await self._record_failures(campaign_id, targets=targets, failures=failures)
            if exhausted:
                return failures
            raise JobRetry("qualification target access unavailable")
        await self._resolve_blocks(campaign_id, chat_ids)
        return ()

    async def _resolve_blocks(self, campaign_id: UUID, chat_ids: tuple[int, ...]) -> None:
        async with self._session_factory() as session, session.begin():
            await resolve_qualification_target_blocks(session, campaign_id, chat_ids)

    async def _degrade_review(
        self,
        campaign_id: UUID,
        participants: list[Participant],
        *,
        failures: tuple[TargetPreflightFailure, ...],
    ) -> None:
        observed_at = utcnow()
        for participant in participants:
            state = _degraded_state(participant.qualification_state)
            await self._participants.record_draw_qualification(
                participant_id=participant.id,
                state=state,
                observed_at=observed_at,
                observed_status="degraded_after_target_unavailable",
                error_code="qualification_target_retry_exhausted",
            )
        chat_ids = tuple(failure.chat_id for failure in failures)
        async with self._session_factory() as session, session.begin():
            await resolve_qualification_target_blocks(session, campaign_id, chat_ids)
            _add_degraded_audit(session, campaign_id, failures, participant_count=len(participants))

    async def _review_participants(
        self,
        participants: list[Participant],
        *,
        targets: tuple[QualificationTarget, ...],
        rules: CampaignRules,
        chat_ids: tuple[int, ...],
        limiter: SmoothRateLimiter,
        attempt_number: int,
    ) -> None:
        for participant in participants:
            observed_at = utcnow()
            result = await observe_memberships(
                self._bot,
                chat_ids=chat_ids,
                user_id=participant.user_id,
                observed_at=observed_at,
                limiter=limiter,
            )
            state, observations = await self._merge_thresholds(
                user_id=participant.user_id,
                rules=rules,
                result=result,
                observed_at=observed_at,
            )
            tagged = _tag_target_kinds(observations, targets, attempt_number=attempt_number)
            await self._participants.record_draw_qualification(
                participant_id=participant.id,
                state=state,
                observed_at=observed_at,
                observed_status=result.observed_status,
                error_code=result.error_code,
                observations=tagged,
            )

    async def _record_failures(
        self,
        campaign_id: UUID,
        *,
        targets: tuple[QualificationTarget, ...],
        failures: tuple[TargetPreflightFailure, ...],
    ) -> bool:
        kinds = {target.chat_id: target.kind for target in targets}
        exhaustion_states = []
        async with self._session_factory() as session, session.begin():
            for failure in failures:
                exhaustion_states.append(
                    await record_qualification_target_failure(
                        session,
                        campaign_id=campaign_id,
                        target_kind=kinds.get(failure.chat_id, "requirement"),
                        chat_id=failure.chat_id,
                        error_code=failure.error_code,
                        details=failure.details,
                    )
                )
            exhausted = all(exhaustion_states)
            _add_failure_audit(session, campaign_id, failures, exhausted=exhausted)
        return exhausted

    async def _merge_thresholds(
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
        if not thresholds:
            return result.state, tuple(result.observations)
        if self._message_stats is None:
            raise RuntimeError("message threshold review requires message stats")
        member_ok = {
            int(item["chat_id"])
            for item in result.observations
            if item["state"] == QualificationState.ELIGIBLE.value
        }
        totals = await self._message_stats.totals(user_id, tuple(thresholds))
        shortfalls = _message_shortfalls(
            member_ok=member_ok,
            thresholds=thresholds,
            totals=totals,
            observed_at=observed_at,
        )
        state = QualificationState.INELIGIBLE if shortfalls else result.state
        return state, tuple(result.observations) + shortfalls

    async def _context(
        self, campaign_id: UUID, *, force: bool
    ) -> tuple[Campaign, tuple[QualificationTarget, ...], list[Participant]]:
        async with self._session_factory() as session:
            campaign = await session.get(Campaign, campaign_id)
            if campaign is None:
                raise ValueError("campaign not found")
            targets = await qualification_targets(session, campaign)
            statement = select(Participant).where(Participant.campaign_id == campaign_id)
            if not force:
                statement = statement.where(~_final_check_exists())
            participants = (await session.scalars(statement)).all()
            return campaign, targets, list(participants)

    async def _target_chat_ids(
        self,
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
        async with self._session_factory() as session:
            group = await session.get(Group, campaign.group_id)
        if group is None:
            raise ValueError("campaign group not found")
        return _required_chat_ids(group.chat_id, rules)


def _final_check_exists():
    return exists(
        select(QualificationCheck.id).where(
            QualificationCheck.participant_id == Participant.id,
            QualificationCheck.phase == "draw",
            QualificationCheck.state.in_(
                [QualificationState.ELIGIBLE, QualificationState.INELIGIBLE]
            ),
        )
    )


def _required_chat_ids(main_chat_id: int, rules: CampaignRules) -> tuple[int, ...]:
    values = [main_chat_id]
    if rules.required_channel_id is not None:
        values.append(rules.required_channel_id)
    values.extend(rules.required_chat_ids)
    return tuple(dict.fromkeys(values))


def _degraded_state(state: QualificationState) -> QualificationState:
    if state in {QualificationState.ELIGIBLE, QualificationState.INELIGIBLE}:
        return state
    return QualificationState.INELIGIBLE


def _tag_target_kinds(
    observations: tuple[dict[str, Any], ...],
    targets: tuple[QualificationTarget, ...],
    *,
    attempt_number: int,
) -> tuple[dict[str, Any], ...]:
    kinds = {target.chat_id: target.kind for target in targets}
    return tuple(
        dict(item)
        | {
            "target_kind": kinds.get(int(item["chat_id"]), "requirement"),
            "attempt_number": attempt_number,
        }
        for item in observations
    )


def _message_shortfalls(
    *,
    member_ok: set[int],
    thresholds: dict[int, int],
    totals: dict[int, int],
    observed_at: datetime,
) -> tuple[dict[str, Any], ...]:
    return tuple(
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


def _add_failure_audit(
    session: object,
    campaign_id: UUID,
    failures: tuple[TargetPreflightFailure, ...],
    *,
    exhausted: bool,
) -> None:
    add_audit(
        session,
        actor_user_id=None,
        action="qualification.target_unavailable",
        aggregate_type="campaign",
        aggregate_id=str(campaign_id),
        details={
            "chat_ids": [failure.chat_id for failure in failures],
            "retry_exhausted": exhausted,
        },
    )


def _add_degraded_audit(
    session: object,
    campaign_id: UUID,
    failures: tuple[TargetPreflightFailure, ...],
    *,
    participant_count: int,
) -> None:
    add_audit(
        session,
        actor_user_id=None,
        action="qualification.review_degraded",
        aggregate_type="campaign",
        aggregate_id=str(campaign_id),
        details={
            "chat_ids": [failure.chat_id for failure in failures],
            "participant_count": participant_count,
            "policy": "last_persisted_final_else_ineligible",
        },
    )
