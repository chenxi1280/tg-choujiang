from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.v30_helpers import FIRST, SECOND, START, rules_for, seed_v3, speech
from tg_lottery.application.activity_bonus_reminders import bonus_cycle_text
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.rules import GroupActivityRule
from tg_lottery.infrastructure.db import Participant, TelegramOutbox


def combined_rules(gate, bonus, *, two_groups=False):
    groups = {str(FIRST): {"daily_messages": gate}}
    if two_groups:
        groups[str(SECOND)] = {"daily_messages": gate}
    rules = rules_for("message", ("activity",), groups=groups)
    rule = GroupActivityRule(
        messages_per_cycle=bonus, daily_cap_mode="unlimited", daily_max_cycles=None
    )
    return rules.model_copy(update={"group_activity_rules": dict.fromkeys(groups, rule)})


@pytest.mark.parametrize(
    ("gate", "bonus", "count", "expected_weight"),
    [(5, 5, 5, 2), (10, 5, 5, 0), (10, 5, 10, 3), (5, 10, 5, 1)],
)
async def test_shared_messages_obey_gate_and_bonus_thresholds(
    session_factory, *, gate, bonus, count, expected_weight
):
    cid, pid = await seed_v3(session_factory, combined_rules(gate, bonus))
    await speech(session_factory, count=count)
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified is (count >= gate)
        entries = await SettlementService(None)._eligible_entries(session, cid)
        assert sum(weight for _participant, weight in entries) == expected_weight
        rows = list(await session.scalars(select(TelegramOutbox)))
        assert len(rows) == (2 if count == 10 else 1)
        text = rows[-1].payload["text"]
        if gate == 5 and bonus == 10:
            assert "全部行为门槛已完成" in text and "再发 5 条 +1 次" in text
        if gate == 10 and count == 5:
            assert "暂存" in rows[-1].payload["ticket_status"]
    await speech(session_factory, count=count, at=START, first_id=1)
    async with session_factory() as session:
        assert len(list(await session.scalars(select(TelegramOutbox)))) == len(rows)


async def test_group_completion_and_remaining_bonus_are_one_notice_then_finish_second_group(
    session_factory,
):
    _cid, pid = await seed_v3(session_factory, combined_rules(5, 10, two_groups=True))
    await speech(session_factory, count=5)
    async with session_factory() as session:
        rows = list(await session.scalars(select(TelegramOutbox)))
        assert len(rows) == 1
        assert "⏳ B群 · 今日 0/5 条" in rows[0].payload["text"]
        assert "✅ 测试群 · 门槛完成" in rows[0].payload["text"]
        assert "测试群: 已加 0 次 · 再发 5 条 +1 次" in rows[0].payload["text"]
        assert not (await session.get(Participant, pid)).admission_qualified
    await speech(session_factory, chat_id=SECOND, count=5)
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
    await speech(session_factory, count=5, first_id=6, at=START + timedelta(seconds=6))
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).ticket_total_cache == 2


def test_capped_historical_bonus_does_not_ask_for_unrewarded_messages():
    rule = GroupActivityRule(messages_per_cycle=5)
    assert "今日已达加成上限" in bonus_cycle_text(rule, 5)
    assert "还差" not in bonus_cycle_text(rule, 10)
    assert "已达上限" in bonus_cycle_text(rule, 10, compact=True)
    assert "再发" not in bonus_cycle_text(rule, 10, compact=True)


async def test_compact_gate_completion_survives_midnight_with_daily_bonus_reset(session_factory):
    _cid, _pid = await seed_v3(session_factory, combined_rules(5, 5, two_groups=True))
    await speech(session_factory, count=5)
    await speech(session_factory, count=5, at=START + timedelta(days=1), first_id=6)
    async with session_factory() as session:
        notices = list(await session.scalars(select(TelegramOutbox)))
    latest = max(notices, key=lambda row: row.payload["occurred_at"])
    assert "✅ 测试群 · 门槛完成" in latest.payload["text"]
    assert "⏳ B群 · 今日 0/5 条" in latest.payload["text"]
    assert "已加 1 次 · 再发 5 条 +1 次" in latest.payload["text"]
    assert "暂存" in latest.payload["ticket_status"]
