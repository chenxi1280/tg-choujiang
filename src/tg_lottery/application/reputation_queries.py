"""Read-only ranking over existing activity ledger facts."""

from dataclasses import dataclass

from sqlalchemy import func, select

from tg_lottery.domain.enums import TicketSource
from tg_lottery.infrastructure.db import Participant, TicketLedger

PAGE_SIZE = 10


@dataclass(frozen=True)
class ReputationRow:
    user_id: int
    name: str
    tickets: int
    rank: int


@dataclass(frozen=True)
class ReputationPage:
    rows: tuple[ReputationRow, ...]
    page: int
    pages: int


class ReputationQueries:
    async def reputation_ranking(self, campaign_id, *, page=0):
        totals = _activity_totals(campaign_id)
        ranked = select(
            totals, func.rank().over(order_by=totals.c.tickets.desc()).label("rank")
        ).subquery()
        async with self._session_factory() as session:
            total = int(await session.scalar(select(func.count()).select_from(totals)) or 0)
            pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(max(0, page), pages - 1)
            rows = await session.execute(
                select(ranked)
                .order_by(ranked.c.tickets.desc(), ranked.c.user_id)
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            return ReputationPage(
                tuple(
                    ReputationRow(int(uid), name, int(tickets), int(rank))
                    for uid, name, tickets, rank in rows
                ),
                page,
                pages,
            )


def _activity_totals(campaign_id):
    return (
        select(
            Participant.user_id.label("user_id"),
            Participant.display_name.label("name"),
            func.sum(TicketLedger.delta).label("tickets"),
        )
        .join(TicketLedger, TicketLedger.participant_id == Participant.id)
        .where(
            Participant.campaign_id == campaign_id,
            TicketLedger.campaign_id == campaign_id,
            TicketLedger.source_type == TicketSource.POPULARITY,
        )
        .group_by(Participant.id)
        .having(func.sum(TicketLedger.delta) > 0)
        .subquery()
    )
