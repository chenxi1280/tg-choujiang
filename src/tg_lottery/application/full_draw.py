from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, func, or_, select

from tg_lottery.application.admission_qualification import behavior_clause
from tg_lottery.application.campaign_notifications import add_campaign_card_state_fanout
from tg_lottery.application.campaign_targets import QualificationTarget, qualification_targets
from tg_lottery.application.campaigns import request_early_cutoff
from tg_lottery.application.records import add_audit, add_job
from tg_lottery.domain.enums import CampaignState, QualificationState, UpdateState
from tg_lottery.domain.errors import SettlementBlocked
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    Participant,
    PollingOffset,
    QualificationCheck,
    ScheduledJob,
    TelegramUpdate,
)

FULL_DRAW_VERIFY_JOB = "full_draw_verify"
_PENDING_UPDATE_STATES = (
    UpdateState.RECEIVED,
    UpdateState.PROCESSING,
    UpdateState.FAILED,
)


async def record_full_draw_candidate(
    session: object,
    *,
    campaign: Campaign,
    observed_at: datetime,
    eligible_count: int,
) -> bool:
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.draw_mode != "full" or rules.participant_target is None:
        return False
    business_key = f"full_draw_verify:{campaign.id}:{campaign.config_revision}"
    existing = await session.scalar(
        select(ScheduledJob.id).where(ScheduledJob.business_key == business_key)
    )
    if existing is not None:
        return False
    add_job(
        session,
        job_type=FULL_DRAW_VERIFY_JOB,
        aggregate_id=str(campaign.id),
        business_key=business_key,
        run_at=observed_at,
        payload={"candidate_at": observed_at.isoformat()},
    )
    add_audit(
        session,
        actor_user_id=None,
        action="campaign.full_draw_threshold_candidate",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        details={"eligible": eligible_count, "target": rules.participant_target},
    )
    return True


async def confirm_full_draw_candidate(
    session: object, *, campaign: Campaign, candidate_at: datetime
) -> bool:
    if campaign.state not in {CampaignState.SCHEDULED, CampaignState.RUNNING}:
        return False
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.draw_mode != "full" or rules.participant_target is None:
        return False
    targets = await qualification_targets(session, campaign)
    await _require_inbox_watermark(
        session, campaign=campaign, candidate_at=candidate_at, targets=targets
    )
    eligible = await _fresh_eligible_count(session, campaign, candidate_at)
    if eligible < rules.participant_target:
        raise SettlementBlocked(
            f"full_draw_candidate_below_target:{eligible}/{rules.participant_target}"
        )
    changed = await request_early_cutoff(
        session,
        campaign=campaign,
        cutoff_at=candidate_at,
        reason=f"满人复核达到 {eligible}/{rules.participant_target}",
        actor_user_id=None,
    )
    if changed:
        await add_campaign_card_state_fanout(session, campaign, state="settling")
    return changed


async def _require_inbox_watermark(
    session: object,
    *,
    campaign: Campaign,
    candidate_at: datetime,
    targets: tuple[QualificationTarget, ...],
) -> None:
    chat_ids = tuple(int(target.chat_id) for target in targets)
    pending = await session.scalar(
        select(TelegramUpdate.id)
        .where(
            TelegramUpdate.event_at <= candidate_at,
            TelegramUpdate.state.in_(_PENDING_UPDATE_STATES),
            or_(
                TelegramUpdate.routing_campaign_id == str(campaign.id),
                TelegramUpdate.routing_chat_id.in_(chat_ids),
            ),
        )
        .limit(1)
    )
    if pending is not None:
        raise SettlementBlocked("full_draw_inbox_watermark_pending")
    fresh_poll = await session.scalar(
        select(PollingOffset.bot_id).where(PollingOffset.last_poll_succeeded_at >= candidate_at)
    )
    if fresh_poll is None:
        raise SettlementBlocked("full_draw_polling_watermark_pending")


async def _fresh_eligible_count(session: object, campaign: Campaign, candidate_at: datetime) -> int:
    fresh_eligible = exists(
        select(QualificationCheck.id).where(
            QualificationCheck.participant_id == Participant.id,
            QualificationCheck.phase == "draw",
            QualificationCheck.state == QualificationState.ELIGIBLE,
            QualificationCheck.observed_at >= candidate_at,
        )
    )
    count = await session.scalar(
        select(func.count(Participant.id)).where(
            Participant.campaign_id == campaign.id,
            Participant.qualification_state == QualificationState.ELIGIBLE,
            behavior_clause(),
            fresh_eligible,
        )
    )
    return int(count or 0)
