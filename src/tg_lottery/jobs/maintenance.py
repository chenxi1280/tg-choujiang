from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_notifications import add_campaign_card_state
from tg_lottery.application.campaigns import request_early_cutoff
from tg_lottery.application.records import add_audit
from tg_lottery.domain.enums import CampaignState, JobState, QualificationState, UpdateState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    Participant,
    ScheduledJob,
    TelegramUpdate,
    TicketLedger,
)

UPDATE_PAYLOAD_RETENTION_DAYS = 7
UPDATE_RETENTION_JOB_TYPE = "telegram_update_retention"
TICKET_RECONCILE_JOB_TYPE = "ticket_cache_reconcile"
FULL_DRAW_RECONCILE_JOB_TYPE = "full_draw_reconcile"
_MAINTENANCE_INTERVAL_DAYS = 1
_FULL_DRAW_RECONCILE_INTERVAL_SECONDS = 60


async def ensure_maintenance_jobs(
    session_factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    async with session_factory() as session, session.begin():
        await schedule_update_retention(session, now)
        await schedule_ticket_reconcile(session, now)
        await ensure_full_draw_reconcile(session, now)


async def schedule_update_retention(session: AsyncSession, run_at: datetime) -> None:
    await _schedule_daily_job(session, UPDATE_RETENTION_JOB_TYPE, run_at)


async def schedule_ticket_reconcile(session: AsyncSession, run_at: datetime) -> None:
    await _schedule_daily_job(session, TICKET_RECONCILE_JOB_TYPE, run_at)


async def schedule_full_draw_reconcile(session: AsyncSession, run_at: datetime) -> None:
    await _schedule_job(session, FULL_DRAW_RECONCILE_JOB_TYPE, run_at)


async def ensure_full_draw_reconcile(session: AsyncSession, run_at: datetime) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(FULL_DRAW_RECONCILE_JOB_TYPE)))
    )
    existing = await session.scalar(
        select(ScheduledJob.id).where(
            ScheduledJob.job_type == FULL_DRAW_RECONCILE_JOB_TYPE,
            ScheduledJob.state.in_((JobState.PENDING, JobState.CLAIMED, JobState.RETRY_SCHEDULED)),
        )
    )
    if existing is None:
        await schedule_full_draw_reconcile(session, run_at)


async def _schedule_daily_job(session: AsyncSession, job_type: str, run_at: datetime) -> None:
    business_key = f"{job_type}:{run_at.date().isoformat()}"
    await _schedule_job(session, job_type, run_at, business_key=business_key)


async def _schedule_job(
    session: AsyncSession,
    job_type: str,
    run_at: datetime,
    *,
    business_key: str | None = None,
) -> None:
    key = business_key or f"{job_type}:{run_at.isoformat()}"
    statement = insert(ScheduledJob).values(
        job_type=job_type,
        aggregate_id="system",
        business_key=key,
        payload={},
        run_at=run_at,
        state=JobState.PENDING,
        attempts=0,
    )
    await session.execute(statement.on_conflict_do_nothing(index_elements=["business_key"]))


async def reconcile_full_draws(session: AsyncSession, now: datetime) -> int:
    campaign_ids = tuple(
        await session.scalars(
            select(Campaign.id).where(
                Campaign.state.in_((CampaignState.SCHEDULED, CampaignState.RUNNING))
            )
        )
    )
    triggered = 0
    for campaign_id in campaign_ids:
        triggered += await _reconcile_full_draw(session, campaign_id, now)
    return triggered


async def _reconcile_full_draw(session: AsyncSession, campaign_id: object, now: datetime) -> int:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None or campaign.state not in {
        CampaignState.SCHEDULED,
        CampaignState.RUNNING,
    }:
        return 0
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.draw_mode != "full" or rules.participant_target is None:
        return 0
    eligible = await session.scalar(
        select(func.count(Participant.id)).where(
            Participant.campaign_id == campaign.id,
            Participant.qualification_state == QualificationState.ELIGIBLE,
        )
    )
    if int(eligible or 0) < rules.participant_target:
        return 0
    add_audit(
        session,
        actor_user_id=None,
        action="campaign.full_draw_recovery_detected",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        reason="满人阈值已达到但截止触发尚未持久化",
        details={"eligible": int(eligible or 0), "target": rules.participant_target},
    )
    changed = request_early_cutoff(
        session,
        campaign=campaign,
        cutoff_at=now,
        reason=f"满人开奖对账达到 {eligible}/{rules.participant_target}",
        actor_user_id=None,
    )
    if changed:
        group = await session.get(Group, campaign.group_id)
        if group is None:
            raise ValueError("full draw campaign group is missing")
        add_campaign_card_state(session, campaign, group.chat_id, state="settling")
    return int(changed)


async def reconcile_ticket_caches(session: AsyncSession) -> int:
    campaign_ids = tuple(await session.scalars(select(Campaign.id).order_by(Campaign.id)))
    repaired = 0
    for campaign_id in campaign_ids:
        repaired += await _reconcile_campaign(session, campaign_id)
    return repaired


async def _reconcile_campaign(session: AsyncSession, campaign_id: object) -> int:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None:
        return 0
    rows = (
        await session.execute(
            select(Participant, func.coalesce(func.sum(TicketLedger.delta), 0))
            .outerjoin(TicketLedger, TicketLedger.participant_id == Participant.id)
            .where(Participant.campaign_id == campaign.id)
            .group_by(Participant.id)
        )
    ).all()
    drifted = [
        (participant, int(total))
        for participant, total in rows
        if total != participant.ticket_total_cache
    ]
    for participant, total in drifted:
        before = participant.ticket_total_cache
        participant.ticket_total_cache = total
        add_audit(
            session,
            actor_user_id=None,
            action="participant.ticket_cache_reconciled",
            aggregate_type="participant",
            aggregate_id=str(participant.id),
            details={"before": before, "after": total},
        )
    return len(drifted)


async def prune_processed_update_payloads(session: AsyncSession, now: datetime) -> int:
    cutoff = now - timedelta(days=UPDATE_PAYLOAD_RETENTION_DAYS)
    statement = (
        update(TelegramUpdate)
        .where(
            TelegramUpdate.state == UpdateState.PROCESSED,
            TelegramUpdate.processed_at <= cutoff,
            TelegramUpdate.payload.is_not(None),
        )
        .values(payload=None)
    )
    result = await session.execute(statement)
    return int(result.rowcount or 0)


def next_maintenance_at(now: datetime) -> datetime:
    return now + timedelta(days=_MAINTENANCE_INTERVAL_DAYS)


def next_full_draw_reconcile_at(now: datetime) -> datetime:
    return now + timedelta(seconds=_FULL_DRAW_RECONCILE_INTERVAL_SECONDS)
