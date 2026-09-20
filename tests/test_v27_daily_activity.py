from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import select

from tests.test_base_activity_integration import messages, seed
from tests.test_v22_group_bonus_integration import _seed_activity_campaign
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.group_bonus_progress import load_group_bonus_progress
from tg_lottery.domain.rules import CampaignRules, GroupActivityRule
from tg_lottery.infrastructure.db import Campaign, Participant, TicketLedger
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_four_messages_unlock_base_and_skipped_day_does_not_erase_earned_bonus(
    session_factory,
):
    campaign_id, participant_id = await seed(session_factory, days=1, messages=4)
    service = ActivityService(partial(UnitOfWork, session_factory))
    await messages(service, day=0, count=3)
    async with session_factory() as session:
        assert not (await session.get(Participant, participant_id)).base_qualified
    await messages(service, day=0, count=8)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        assert participant.base_qualified
        assert participant.ticket_total_cache == 2
    await messages(service, day=2, count=4)
    progress = await load_group_bonus_progress(
        session_factory, campaign_id, 201, observed_at=datetime(2026, 9, 3, 4, tzinfo=UTC)
    )
    activity = progress.activities[0]
    assert activity.awarded_tickets == 1
    assert activity.total_awarded_tickets == 2
    assert activity.qualified_days == 2
    async with session_factory() as session:
        assert (await session.get(Participant, participant_id)).ticket_total_cache == 3


@pytest.mark.parametrize("threshold", [4, 5])
async def test_beijing_midnight_resets_daily_count_but_preserves_total(session_factory, threshold):
    before = datetime(2026, 9, 20, 15, 55, tzinfo=UTC)
    midnight = datetime(2026, 9, 20, 16, tzinfo=UTC)
    rules = CampaignRules(
        bonus_types=("activity",),
        group_activity_rules={"-100123": GroupActivityRule(messages_per_cycle=threshold)},
    )
    campaign_id, participant_id = await _seed_activity_campaign(session_factory, rules, before)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.preheat_at = before - timedelta(hours=1)
        campaign.cutoff_at = midnight + timedelta(hours=1)
    service = ActivityService(partial(UnitOfWork, session_factory))
    await _send(service, before, start_id=1, count=threshold * 2)
    progress = await load_group_bonus_progress(
        session_factory, campaign_id, 201, observed_at=midnight
    )
    assert progress.activities[0].valid_messages == 0
    assert progress.activities[0].awarded_tickets == 0
    assert progress.activities[0].total_awarded_tickets == 1
    await _send(service, midnight, start_id=100, count=threshold)
    progress = await load_group_bonus_progress(
        session_factory, campaign_id, 201, observed_at=midnight + timedelta(minutes=1)
    )
    assert progress.activities[0].qualified_days == 2
    assert progress.activities[0].total_awarded_tickets == 2
    async with session_factory() as session:
        ledger = (
            await session.scalars(
                select(TicketLedger).where(TicketLedger.participant_id == participant_id)
            )
        ).all()
        assert sum(row.delta for row in ledger) == 2


async def _send(service, when, *, start_id, count):
    for number in range(count):
        observation = MessageObservation(
            -100123,
            start_id + number,
            201,
            when + timedelta(seconds=number),
            f"有效发言内容:{start_id + number}号消息",
        )
        await service.record_message(observation)
        await service.record_message(observation)
