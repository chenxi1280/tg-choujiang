from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message

from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.creator_views import preview_text
from tg_lottery.telegram.draft_config import new_draft


def _complete_draft():
    return new_draft() | {
        "chat_id": -1001,
        "admission": {"type": "message", "groups": {"-1001": {"daily_messages": 1}}},
        "group_title": "测试群",
        "title": "旧测试活动",
        "prizes": [{"name": "测试奖品", "winner_count": 1}],
        "draw_at": "2030-01-01T12:00:00+00:00",
        "creator_name": "创建者",
    }


@pytest.mark.parametrize("offset", [timedelta(minutes=-1), timedelta(minutes=4)])
def test_preview_does_not_claim_publishable_when_draw_has_expired_or_has_no_drain_window(offset):
    now = datetime(2030, 1, 1, 12, tzinfo=UTC)
    data = _complete_draft() | {"draw_at": (now + offset).isoformat()}
    text = preview_text(data, now=now)
    assert "可以发布" not in text
    assert "开奖时间至少需要晚于当前时间 5 分钟" in text


def test_preview_and_publish_validation_agree_for_future_draw():
    now = datetime(2030, 1, 1, 11, tzinfo=UTC)
    assert "可以发布" in preview_text(_complete_draft(), now=now)


async def test_template_restores_business_config_but_requires_new_draw_time():
    current = new_draft() | {"creator_name": "创建者", "panel_message_id": 123}
    previous = _complete_draft()
    sessions, renderer = ThresholdSessions(current), RecordingRenderer()
    settings = CreatorSettings(
        sessions=sessions,
        renderer=renderer,
        profiles=SimpleNamespace(recent_last_draft=AsyncMock(return_value=previous)),
        queries=object(),
        bot=object(),
    )
    message = Message(message_id=123, date=datetime.now(UTC), chat=Chat(id=101, type="private"))
    query = SimpleNamespace(from_user=SimpleNamespace(id=101), message=message)
    await settings.handle_grant(query, CallbackGrant("draft_load_last", None, {}))
    assert sessions.data == current
    assert "确认复用" in renderer.text
    await settings.handle_grant(query, CallbackGrant("draft_load_last_confirm", None, {}))
    assert sessions.data["draw_at"] == ""
    assert sessions.data["title"] == previous["title"]
    assert sessions.data["prizes"] == previous["prizes"]
    assert sessions.data["draft_id"] == current["draft_id"]
    assert sessions.data["panel_message_id"] == 123
    assert "开奖时间" in renderer.text
    assert "可以发布" not in renderer.text
    assert previous["draw_at"] == "2030-01-01T12:00:00+00:00"
