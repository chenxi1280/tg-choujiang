from datetime import UTC, datetime

from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.domain.enums import TicketSource
from tg_lottery.infrastructure.db import (
    AuditLog,
    Participant,
    ScheduledJob,
    TicketLedger,
)
from tg_lottery.jobs.maintenance import (
    reconcile_ticket_caches,
    schedule_ticket_reconcile,
)


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
