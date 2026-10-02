from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_creator_settings import ThresholdSessions, _message, _panel_query
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_participation import participation_add_text
from tg_lottery.telegram.creator_schedule import MIN_DRAW_LEAD, validate_draw_time
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.creator_settings_ui import custom_draw_prompt
from tg_lottery.telegram.draft_config import new_draft, parse_draw_at


@pytest.mark.parametrize("value", ["2026/10/03 20:00", "2026-10-03", "明天晚上八点"])
def test_draw_time_format_error_has_no_stale_example(value):
    with pytest.raises(UserFacingError, match="开奖时间格式应为") as error:
        parse_draw_at(value, "Europe/London")
    assert "北京时间" in str(error.value)
    assert "2026-08" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30 20:00",
        "2026-02-29 20:00",
        "2026-13-03 20:00",
        "2026-10-03 24:00",
        "2026-10-03 20:60",
    ],
)
def test_draw_time_calendar_error_explains_invalid_date_or_time(value):
    with pytest.raises(UserFacingError, match="开奖日期或时间无效") as error:
        parse_draw_at(value, "Europe/London")
    assert "北京时间" in str(error.value)
    assert "格式应为" not in str(error.value)


def test_draw_time_accepts_leap_day_and_existing_single_digit_input_in_beijing():
    assert parse_draw_at("2028-02-29 20:00", "Europe/London").isoformat() == (
        "2028-02-29T20:00:00+08:00"
    )
    assert parse_draw_at(" 2026-10-3 9:5 ", "UTC").isoformat() == ("2026-10-03T09:05:00+08:00")


def test_draw_time_lead_error_is_separate_from_calendar_and_format_errors():
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    at_limit = now + MIN_DRAW_LEAD
    with pytest.raises(UserFacingError, match="至少需要晚于当前时间 5 分钟") as error:
        validate_draw_time(at_limit, now=now)
    assert "北京时间" in str(error.value)
    validate_draw_time(at_limit + timedelta(seconds=1), now=now)


@pytest.mark.parametrize("action", ["draft_draw", "draft_draw_fixed", "draft_draw_custom"])
async def test_current_and_existing_time_buttons_open_input_without_mode_page(action):
    data = new_draft() | {"title": "保留的标题", "draw_at": "2026-10-03T20:00:00+08:00"}
    before = deepcopy(data)
    sessions = ThresholdSessions(data)
    renderer = SimpleNamespace(render=AsyncMock(), edit=AsyncMock())
    settings = _settings(sessions, renderer)

    assert await settings.handle_grant(_panel_query(), SimpleNamespace(action=action))

    assert sessions.state == "draft:draw"
    assert sessions.data["title"] == before["title"]
    assert sessions.data["draw_at"] == before["draw_at"]
    values = renderer.render.await_args.kwargs
    assert "已保存的开奖时间" in values["text"]
    assert "<code>2026-10-03 20:00</code>" in values["text"]
    assert values["rows"][-1] == [("↩ 返回草稿", "draft_back")]
    renderer.edit.assert_not_awaited()


async def test_invalid_calendar_input_preserves_saved_draft_and_input_state():
    data = new_draft() | {"title": "保留的标题", "draw_at": "2026-10-03T20:00:00+08:00"}
    sessions = ThresholdSessions(data)
    sessions.state = "draft:draw"
    renderer = SimpleNamespace(render=AsyncMock(), edit=AsyncMock())
    settings = _settings(sessions, renderer)
    before = deepcopy(sessions.data)

    with pytest.raises(UserFacingError, match="日期或时间无效"):
        await settings.handle_message(_message("2026-02-30 20:00"), sessions.state, sessions.data)

    assert sessions.data == before
    assert sessions.state == "draft:draw"
    renderer.edit.assert_not_awaited()


async def test_existing_full_draw_button_still_explicitly_rejects_without_rewriting_draft():
    data = new_draft() | {"draw_mode": "full", "participant_target": 100}
    sessions = ThresholdSessions(data)
    before = deepcopy(data)
    settings = _settings(sessions, SimpleNamespace(render=AsyncMock(), edit=AsyncMock()))

    with pytest.raises(UserFacingError, match="满人开奖已取消"):
        await settings.handle_grant(_panel_query(), SimpleNamespace(action="draft_draw_full"))
    assert sessions.data == before


def test_saved_draw_time_is_labelled_as_saved_instead_of_current_clock():
    text = custom_draw_prompt("UTC", saved="2026-10-03T12:00:00+00:00")
    assert "已保存的开奖时间" in text
    assert "当前时间" not in text
    assert "<code>2026-10-03 20:00</code>" in text


def test_admission_prompt_prioritizes_list_and_preserves_other_input_methods():
    text = participation_add_text()
    assert text.index("点击下方列表") < text.index("也可直接发送")
    assert "搜索名称" in text and "返回条件列表" in text
    assert "t.me/c/..." in text and "转发来源可见" in text
    assert "数字 chat ID 仅用于高级操作" in text


def _settings(sessions, renderer):
    return CreatorSettings(
        sessions=sessions, renderer=renderer, profiles=object(), queries=object(), bot=object()
    )
