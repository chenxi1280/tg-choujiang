from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.records import add_audit
from tg_lottery.domain.enums import JobState, UpdateState
from tg_lottery.infrastructure.db import (
    Campaign,
    Participant,
    ScheduledJob,
    TelegramUpdate,
    TicketLedger,
)

UPDATE_PAYLOAD_RETENTION_DAYS = 7
UPDATE_RETENTION_JOB_TYPE = "telegram_update_retention"
TICKET_RECONCILE_JOB_TYPE = "ticket_cache_reconcile"
_MAINTENANCE_INTERVAL_DAYS = 1


async def ensure_maintenance_jobs(
    session_factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    async with session_factory() as session, session.begin():
        await schedule_update_retention(session, now)
        await schedule_ticket_reconcile(session, now)


async def schedule_update_retention(session: AsyncSession, run_at: datetime) -> None:
    await _schedule_daily_job(session, UPDATE_RETENTION_JOB_TYPE, run_at)


async def schedule_ticket_reconcile(session: AsyncSession, run_at: datetime) -> None:
    await _schedule_daily_job(session, TICKET_RECONCILE_JOB_TYPE, run_at)


async def _schedule_daily_job(session: AsyncSession, job_type: str, run_at: datetime) -> None:
    business_key = f"{job_type}:{run_at.date().isoformat()}"
    statement = insert(ScheduledJob).values(
        job_type=job_type,
        aggregate_id="system",
        business_key=business_key,
        payload={},
        run_at=run_at,
        state=JobState.PENDING,
        attempts=0,
    )
    await session.execute(statement.on_conflict_do_nothing(index_elements=["business_key"]))


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
