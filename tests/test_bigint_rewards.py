import pytest
from sqlalchemy import func, select

from tests.test_v33_unlimited_runtime import current_rules
from tests.v30_helpers import FIRST, seed_v3, speech
from tg_lottery.domain.enums import TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import DailyProgress, Participant, TicketLedger


@pytest.mark.parametrize("reward", [2**31 - 1, 2**31])
async def test_large_activity_reward_is_durable_and_replay_safe(session_factory, reward):
    raw = current_rules().model_dump(mode="json")
    raw["group_activity_rules"][str(FIRST)]["reward_tickets"] = reward
    _, participant_id = await seed_v3(session_factory, CampaignRules.model_validate(raw))
    # speech replays each observation, so each cycle must still be credited once.
    await speech(session_factory, count=4)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        progress = await session.scalar(select(DailyProgress))
        rewards = tuple(
            await session.scalars(
                select(TicketLedger.delta).where(
                    TicketLedger.source_type == TicketSource.POPULARITY
                )
            )
        )
        assert rewards == (reward, reward)
        assert progress.tickets_awarded == 2 * reward
        assert participant.ticket_total_cache == 1 + 2 * reward
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 1 + 2 * reward
