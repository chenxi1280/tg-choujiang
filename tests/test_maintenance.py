from datetime import UTC, datetime

from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.domain.enums import CampaignState, TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    AuditLog,
    Campaign,
    Participant,
    ScheduledJob,
    TicketLedger,
)
from tg_lottery.jobs.maintenance import (
    FULL_DRAW_RECONCILE_JOB_TYPE,
    ensure_full_draw_reconcile,
    reconcile_full_draws,
    reconcile_ticket_caches,
    schedule_ticket_reconcile,
)


async def test_full_draw_reconcile_triggers_missed_threshold_once(session_factory) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(draw_mode="full", participant_target=1)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, rules=rules, state=CampaignState.RUNNING)
        campaign_id = campaign.id
    async with session_factory() as session, session.begin():
        assert await reconcile_full_draws(session, now) == 1
    async with session_factory() as session, session.begin():
        assert await reconcile_full_draws(session, now) == 0
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        job_count = await session.scalar(select(func.count(ScheduledJob.id)))
        candidate = await session.scalar(
            select(AuditLog).where(AuditLog.action == "campaign.full_draw_threshold_candidate")
        )
        recovery = await session.scalar(
            select(AuditLog).where(AuditLog.action == "campaign.full_draw_recovery_detected")
        )
    assert campaign is not None and campaign.state is CampaignState.RUNNING
    assert campaign.cutoff_at > now
    assert job_count == 1
    assert candidate is not None and candidate.actor_user_id is None
    assert recovery.details == {"eligible": 1, "target": 1}


async def test_full_draw_reconcile_startup_seeds_only_one_active_chain(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        await ensure_full_draw_reconcile(session, now)
    async with session_factory() as session, session.begin():
        await ensure_full_draw_reconcile(session, now.replace(microsecond=0))
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(ScheduledJob.id)).where(
                ScheduledJob.job_type == FULL_DRAW_RECONCILE_JOB_TYPE
            )
        )
    assert count == 1


async def test_ticket_cache_reconcile_repairs_cache_without_changing_ledger(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        participant.ticket_total_cache = 99
        session.add(
            TicketLedger(
                campaign_id=campaign.id,
                participant_id=participant.id,
                delta=3,
                source_type=TicketSource.POPULARITY,
                source_ref="reconcile-seed",
                business_key="reconcile-seed",
                occurred_at=now,
            )
        )
        participant_id = participant.id
    async with session_factory() as session, session.begin():
        assert await reconcile_ticket_caches(session) == 1
        await schedule_ticket_reconcile(session, now)
        await schedule_ticket_reconcile(session, now)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        ledger_total = await session.scalar(select(func.sum(TicketLedger.delta)))
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "participant.ticket_cache_reconciled")
        )
        job_count = await session.scalar(select(func.count(ScheduledJob.id)))
        assert participant.ticket_total_cache == 3
        assert ledger_total == 3
        assert audit.details == {"before": 99, "after": 3}
        assert job_count == 1
