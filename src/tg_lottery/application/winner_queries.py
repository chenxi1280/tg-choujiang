from dataclasses import dataclass

from sqlalchemy import select

from tg_lottery.infrastructure.db import DrawRound, Participant, Prize, Winner


@dataclass(frozen=True)
class WinnerRow:
    winner: Winner
    participant: Participant
    prize: Prize
    slot: int


async def load_winners(factory, campaign_id):
    async with factory() as session:
        rows = await session.execute(
            select(Winner, Participant, Prize, DrawRound.prize_slot_no)
            .join(Participant, Participant.id == Winner.participant_id)
            .join(Prize, Prize.id == Winner.prize_id)
            .join(DrawRound, DrawRound.id == Winner.draw_round_id)
            .where(Prize.campaign_id == campaign_id)
            .order_by(Winner.created_at)
        )
        return [WinnerRow(*row) for row in rows]


WINNER_PAGE_SIZE = 6


@dataclass(frozen=True)
class WinnerPage:
    rows: tuple[WinnerRow, ...]
    page: int
    pages: int
    total: int


async def load_winner_page(factory, campaign_id, page):
    from sqlalchemy import func

    async with factory() as session:
        statement = (
            select(Winner, Participant, Prize, DrawRound.prize_slot_no)
            .join(Participant, Participant.id == Winner.participant_id)
            .join(Prize, Prize.id == Winner.prize_id)
            .join(DrawRound, DrawRound.id == Winner.draw_round_id)
            .where(Prize.campaign_id == campaign_id)
        )
        total = int(await session.scalar(select(func.count()).select_from(statement.subquery())))
        pages = max(1, (total + WINNER_PAGE_SIZE - 1) // WINNER_PAGE_SIZE)
        page = min(max(0, page), pages - 1)
        rows = await session.execute(
            statement.order_by(Prize.position, DrawRound.prize_slot_no, Winner.id)
            .offset(page * WINNER_PAGE_SIZE)
            .limit(WINNER_PAGE_SIZE)
        )
        return WinnerPage(tuple(WinnerRow(*row) for row in rows), page, pages, total)
