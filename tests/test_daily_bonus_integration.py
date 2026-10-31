from datetime import timedelta

import pytest
from sqlalchemy import func, select

from tests.v30_helpers import FIRST, SECOND, START, rules_for, seed_v3, speech
from tg_lottery.application.activity_bonus_reminders import activity_bonus_progress_text
from tg_lottery.application.group_bonus_progress import load_group_bonus_progress
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Campaign, Participant, TelegramOutbox, TicketLedger


def daily_rules(*, double=True, mode="daily", threshold=2, reward=3, groups=(FIRST,)):
    raw = rules_for("message", ("activity",)).model_dump(mode="json")
    raw |= {"bonus_policy_version": "daily-after-start-v1", "activity_bonus_double_count": double}
    raw["invite"]["retention_hours"] = 24
    raw["admission"]["groups"] = {
        str(chat): {"count_mode": mode, "daily_messages": threshold, "consecutive_days": 3}
        for chat in groups
    }
    raw["group_activity_rules"] = {
        str(chat): {
            "messages_per_cycle": reward,
            "daily_cap_mode": "limited",
            "daily_max_cycles": 1,
        }
        for chat in groups
    }
    return CampaignRules.model_validate(raw)


async def totals(factory, pid):
    async with factory() as session:
        participant = await session.get(Participant, pid)
        total = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(TicketLedger.participant_id == pid)
        )
        assert total == participant.ticket_total_cache
        return total


@pytest.mark.parametrize("double,needed", [(True, 3), (False, 5)])
async def test_first_day_daily_limit_reset_and_replayed_messages(session_factory, double, needed):
    cid, pid = await seed_v3(session_factory, daily_rules(double=double))
    await speech(session_factory, count=7)
    assert await totals(session_factory, pid) == 1
    at = START + timedelta(days=1)
    await speech(session_factory, count=needed - 1, at=at, first_id=100)
    assert await totals(session_factory, pid) == 1
    await speech(session_factory, count=1, at=at + timedelta(minutes=1), first_id=200)
    assert await totals(session_factory, pid) == 2
    await speech(session_factory, count=7, at=at + timedelta(minutes=2), first_id=300)
    assert await totals(session_factory, pid) == 2
    progress = await load_group_bonus_progress(
        session_factory, cid, 201, observed_at=at + timedelta(hours=1)
    )
    assert progress.daily_bonus.awarded == 1
    async with session_factory() as session:
        campaign = await session.get(Campaign, cid)
        participant = await session.get(Participant, pid)
        text = await activity_bonus_progress_text(
            session,
            campaign,
            participant,
            event_at=at + timedelta(hours=1),
            group_label=lambda key, name: f"linked:{key}:{name}",
        )
        assert "linked:-100123:" in text
        assert "本群发票前置条件已满足" in text
    async with session_factory() as session:
        texts = await session.scalars(select(TelegramOutbox.payload))
        assert any("抽奖加成票(发言) +1" in row.get("text", "") for row in texts)
    await speech(session_factory, count=needed, at=START + timedelta(days=2), first_id=400)
    assert await totals(session_factory, pid) == 3


@pytest.mark.parametrize("double,extra", [(True, 0), (False, 2)])
async def test_period_gate_crosses_days_and_non_double_starts_after_boundary(
    session_factory, double, extra
):
    cid, pid = await seed_v3(
        session_factory, daily_rules(double=double, mode="period", threshold=6, reward=2)
    )
    await speech(session_factory, count=4)
    at = START + timedelta(days=1)
    await speech(session_factory, count=1, at=at, first_id=100)
    assert await totals(session_factory, pid) == 1
    await speech(session_factory, count=1, at=at + timedelta(minutes=1), first_id=200)
    progress = await load_group_bonus_progress(
        session_factory, cid, 201, observed_at=at + timedelta(hours=1)
    )
    assert progress.daily_bonus.groups[0].messages == (2 if double else 0)
    if extra:
        assert await totals(session_factory, pid) == 1
        await speech(session_factory, count=extra, at=at + timedelta(minutes=2), first_id=300)
    assert await totals(session_factory, pid) == 2
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
    await speech(session_factory, count=2, at=START + timedelta(days=2), first_id=400)
    assert await totals(session_factory, pid) == 3


@pytest.mark.parametrize("double", [True, False])
async def test_period_gate_finished_first_day_still_waits_for_second_day(session_factory, double):
    _, pid = await seed_v3(
        session_factory, daily_rules(double=double, mode="period", threshold=3, reward=2)
    )
    await speech(session_factory, count=5)
    assert await totals(session_factory, pid) == 1
    await speech(session_factory, count=2, at=START + timedelta(days=1), first_id=100)
    assert await totals(session_factory, pid) == 2


async def test_two_groups_award_only_one_campaign_ticket(session_factory):
    _, pid = await seed_v3(session_factory, daily_rules(groups=(FIRST, SECOND)))
    at = START + timedelta(days=1)
    await speech(session_factory, count=6, at=at)
    assert await totals(session_factory, pid) == 1
    await speech(session_factory, chat_id=SECOND, count=6, at=at, first_id=100)
    assert await totals(session_factory, pid) == 2
    await speech(session_factory, count=6, at=at + timedelta(minutes=1), first_id=200)
    assert await totals(session_factory, pid) == 2


async def test_frozen_unlimited_campaign_still_awards_first_day_multiple_times(session_factory):
    _, pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    await speech(session_factory, count=6)
    assert await totals(session_factory, pid) == 4
