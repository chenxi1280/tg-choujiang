from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_creator_settings import RequiredChatBot, ThresholdSessions, _message, _panel_query
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_admission_picker import CreatorAdmissionPicker
from tg_lottery.telegram.creator_participation import participation_rows
from tg_lottery.telegram.creator_publication import _require_fixed_draw
from tg_lottery.telegram.creator_schedule import quick_draw_at
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.creator_settings_ui import custom_draw_rows, draw_mode_rows
from tg_lottery.telegram.creator_share import publication_receipt


def picker_fixture(*, data=None, bot=None):
    sessions = ThresholdSessions({"panel_message_id": 9, **(data or {})})
    renderer = SimpleNamespace(edit=AsyncMock(), render=AsyncMock())
    groups = [SimpleNamespace(chat_id=-1000 - i, title=f"群{i:02}") for i in range(1, 15)]
    queries = SimpleNamespace(admin_groups=AsyncMock(return_value=groups))
    picker = CreatorAdmissionPicker(
        bot=bot or RequiredChatBot(), sessions=sessions, queries=queries, renderer=renderer
    )
    return picker, sessions, renderer


def grant(action):
    return CallbackGrant("draft_admission:" + action, None, {})


def test_admission_empty_page_only_has_add_and_back():
    assert participation_rows({}) == [
        [("添加准入条件", "draft_participation_add")],
        [("↩ 返回上一层", "draft_back")],
    ]


async def test_picker_search_pagination_and_clear_preserve_draft():
    picker, sessions, renderer = picker_fixture(data={"title": "草稿", "chat_id": -1001})
    await picker.show(_panel_query(), None)
    assert "共 13 项" in renderer.edit.await_args.kwargs["text"]
    assert "群01" not in str(renderer.edit.await_args.kwargs["rows"])
    await picker.handle(_panel_query(), grant("next"))
    assert sessions.data["admission_page"] == 1
    assert "上一页" in str(renderer.edit.await_args.kwargs["rows"])
    await picker.handle(_panel_query(), grant("prev"))
    assert sessions.data["admission_page"] == 0
    await picker.handle(_panel_query(), grant("search"))
    assert sessions.state == "draft:admission_search"
    await picker.receive(_message("群14"), sessions.data, searching=True)
    assert "共 1 项" in renderer.edit.await_args.kwargs["text"]
    assert "群14" in str(renderer.edit.await_args.kwargs["rows"])
    await picker.receive(_message("不存在"), sessions.data, searching=True)
    assert "没有匹配" in renderer.edit.await_args.kwargs["text"]
    await picker.handle(_panel_query(), grant("clear_search"))
    assert sessions.data["title"] == "草稿"
    assert sessions.data["admission_search"] == ""


async def test_picker_select_toggle_and_permission_failure_are_atomic():
    picker, sessions, _ = picker_fixture(data={"chat_id": -1001})
    await picker.handle(_panel_query(), grant("select:-1003"))
    assert sessions.data["required_chat_ids"] == [-1003]
    await picker.handle(_panel_query(), grant("select:-1003"))
    assert sessions.data["required_chat_ids"] == []
    with pytest.raises(UserFacingError, match="参与活动的群"):
        await picker.handle(_panel_query(), grant("select:-1001"))
    picker, sessions, _ = picker_fixture(bot=RequiredChatBot(creator_status="member"))
    before = dict(sessions.data)
    with pytest.raises(UserFacingError, match="创建者必须"):
        await picker.handle(_panel_query(), grant("select:-1003"))
    assert sessions.data == before


@pytest.mark.parametrize("attribute", ["chat", "sender_chat"])
async def test_forward_source_selects_chat_in_picker_and_search(attribute):
    picker, sessions, _ = picker_fixture()
    message = _message("不把转发正文误当成群链接")
    message.forward_origin = SimpleNamespace(**{attribute: SimpleNamespace(id=-1003)})
    await picker.receive(message, sessions.data, searching=True)
    assert sessions.data["required_chat_ids"] == [-1003]
    assert sessions.state == "draft:admission_picker"


async def test_hidden_forward_source_is_not_guessed_from_message_text():
    picker, sessions, _ = picker_fixture()
    message = _message("@public_group")
    message.forward_origin = SimpleNamespace(sender_user_name="转发者")
    with pytest.raises(UserFacingError, match="没有携带群聊或频道来源"):
        await picker.receive(message, sessions.data)
    assert "required_chat_ids" not in sessions.data


async def test_admission_picker_callbacks_and_message_states_are_wired():
    picker, sessions, renderer = picker_fixture()
    settings = CreatorSettings(
        sessions=sessions,
        renderer=renderer,
        profiles=object(),
        queries=picker._queries,
        bot=RequiredChatBot(),
    )
    assert settings.handles_action("draft_admission:next")
    await settings.handle_grant(_panel_query(), CallbackGrant("draft_participation_add", None, {}))
    assert sessions.state == "draft:admission_picker"
    assert await settings.handle_message(_message("-1003"), sessions.state, sessions.data)
    assert sessions.data["required_chat_ids"] == [-1003]


@pytest.mark.parametrize("action,days", [("draft_draw_week", 7), ("draft_draw_ten_days", 10)])
def test_quick_days_preserve_beijing_clock_across_month(action, days):
    now = datetime(2026, 9, 28, 15, 45, tzinfo=UTC)
    target = quick_draw_at(action, "Europe/London", now=now)
    assert target.astimezone(UTC) == now + timedelta(days=days)
    assert (target.hour, target.minute) == (23, 45)


def test_draw_options_remove_full_and_copy_and_preserve_tomorrow():
    assert "draft_draw_full" not in str(draw_mode_rows())
    assert custom_draw_rows("UTC")[:2] == [
        [("7天后", "draft_draw_week"), ("10天后", "draft_draw_ten_days")],
        [("明晚20:00", "draft_draw_tomorrow")],
    ]
    target = quick_draw_at("draft_draw_tomorrow", "UTC", now=datetime(2026, 9, 30, 15, tzinfo=UTC))
    assert (target.month, target.day, target.hour) == (10, 1, 20)
    with pytest.raises(UserFacingError, match="满人开奖已取消"):
        _require_fixed_draw({"draw_mode": "full"})
    with pytest.raises(UserFacingError, match="快捷时间已更新"):
        quick_draw_at("draft_draw_hour", "UTC")


def test_publication_receipt_keeps_join_link_when_forwarded_without_buttons():
    from urllib.parse import parse_qs, urlparse

    text, keyboard = publication_receipt(SimpleNamespace(name="抽奖", public_ref="abc"), "test_bot")
    link = keyboard.inline_keyboard[0][0].url
    assert link == "https://t.me/test_bot?start=join_abc"
    assert link in text and "长按" in text
    assert "发布请求已提交" in text and "确认送达后" in text
    share_query = parse_qs(urlparse(keyboard.inline_keyboard[1][0].url).query)
    assert share_query["url"] == [link]
