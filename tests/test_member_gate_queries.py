from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tg_lottery.application import admission_views, member_gate_queries


@pytest.mark.parametrize("mode", ["message", "invite"])
async def test_gate_group_lines_loads_each_group_without_custom_labels(monkeypatch, mode):
    groups = {"-1001": {}, "-1002": {}}
    campaign = SimpleNamespace(
        id=uuid4(),
        rules={
            "participation_rules_version": "v3.0",
            "admission": {"type": mode, "groups": groups},
        },
        presentation={
            "participation_groups": [
                {"id": "-1001", "title": "A群"},
                {"id": "-1002", "title": "B群"},
            ]
        },
    )
    participant = SimpleNamespace(id=uuid4())
    result = SimpleNamespace(all=lambda: [(-1001, 4), (-1002, 2)])
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=participant), execute=AsyncMock(return_value=result)
    )

    class Factory:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(
        member_gate_queries, "resolve_campaign_reference", AsyncMock(return_value=campaign)
    )
    monkeypatch.setattr(member_gate_queries, "_completion_records", AsyncMock(return_value={}))
    monkeypatch.setattr(
        member_gate_queries,
        "_progress_observed_at",
        AsyncMock(return_value=datetime(2026, 10, 29, tzinfo=UTC)),
    )
    monkeypatch.setattr(member_gate_queries, "_progress_completed", lambda *_: False)
    progress = {
        "-1001": SimpleNamespace(today_messages=5, consecutive_days=0),
        "-1002": SimpleNamespace(today_messages=7, consecutive_days=0),
    }
    monkeypatch.setattr(admission_views, "load_base_progress", AsyncMock(return_value=progress))

    lines = await member_gate_queries.load_gate_group_lines(Factory, 202, "campaign-ref")

    assert list(lines) == ["-1001", "-1002"]
    assert lines["-1001"].startswith("A群:")
    assert lines["-1002"].startswith("B群:")
    if mode == "message":
        assert "今日 5/20 条" in lines["-1001"]
        assert "今日 7/20 条" in lines["-1002"]
    else:
        assert "已确认 4/1 人" in lines["-1001"]
        assert "已确认 2/1 人" in lines["-1002"]


async def test_gate_group_lines_reads_real_message_progress(session_factory, monkeypatch):
    from tests.v30_helpers import FIRST, SECOND, START, rules_for, seed_v3, speech
    from tg_lottery.infrastructure.db import Campaign

    rules = rules_for(
        "message", groups={str(FIRST): {"daily_messages": 2}, str(SECOND): {"daily_messages": 3}}
    )
    campaign_id, _ = await seed_v3(session_factory, rules)
    await speech(session_factory, chat_id=FIRST, count=1)
    async with session_factory() as session:
        reference = (await session.get(Campaign, campaign_id)).public_ref
    monkeypatch.setattr(member_gate_queries, "utcnow", lambda: START + timedelta(minutes=1))

    lines = await member_gate_queries.load_gate_group_lines(session_factory, 201, reference)

    assert "今日 1/2 条" in lines[str(FIRST)]
    assert "今日 0/3 条" in lines[str(SECOND)]
