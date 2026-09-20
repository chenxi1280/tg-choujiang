from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.v22_bonus_test_helpers import draft_data, picker_query
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_base_editor as base
from tg_lottery.telegram.creator_base_activity import CreatorBaseActivity
from tg_lottery.telegram.creator_group_bonus import apply_action, editor_view, receive_input
from tg_lottery.telegram.creator_renderer import CreatorRenderer
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.creator_settings_ui import custom_draw_prompt, custom_draw_rows
from tg_lottery.telegram.creator_threshold_input import apply_threshold_input
from tg_lottery.telegram.draft_config import rules_from_data


def test_base_buttons_save_four_messages_and_cancel_custom_without_mutating_saved_rule():
    original = draft_data()
    editor = base.apply_action(original, "draft_base:group:-1001")
    text, rows = base.editor_view(editor)
    assert "4条" in [label for row in rows for label, _ in row]
    assert "保存后生效" in text
    saved = base.apply_action(editor, "draft_base:save")
    rule = rules_from_data(saved).base_activity_rules["-1001"]
    assert (rule.consecutive_days, rule.daily_messages, rule.min_text_length) == (1, 4, 1)
    before = deepcopy(saved)
    editing = base.apply_action(saved, "draft_base:group:-1001")
    editing = base.apply_action(editing, "draft_base:input:daily_messages")
    editing = base.receive_value(editing, "17")
    assert editing[base.EDITOR_KEY]["rule"]["daily_messages"] == 17
    cancelled = base.apply_action(editing, "draft_base:list")
    assert cancelled == before
    assert saved == before
    assert not original.get("base_activity_rules")


def test_removed_group_cannot_be_saved_and_custom_input_requires_a_selected_field():
    editor = base.apply_action(draft_data(), "draft_base:group:-1001")
    with pytest.raises(UserFacingError, match="自定义"):
        base.receive_value(editor, "5")
    with pytest.raises(UserFacingError, match="已移除"):
        base.apply_action(editor | {"participation_groups": []}, "draft_base:save")


async def test_base_controller_routes_custom_input_and_checks_permissions_only_on_save(monkeypatch):
    sessions = ThresholdSessions(draft_data())
    renderer = RecordingRenderer()
    verify = AsyncMock()
    monkeypatch.setattr(
        "tg_lottery.telegram.creator_base_activity.verify_publication_bonus_permissions", verify
    )
    controller = CreatorBaseActivity(sessions=sessions, renderer=renderer, bot=object())
    query = picker_query()
    for action in ("draft_base:group:-1002", "draft_base:input:daily_messages"):
        await controller.handle(query, SimpleNamespace(action=action))
    message = SimpleNamespace(from_user=query.from_user, chat=query.message.chat, text="5")
    await controller.receive(message, sessions.data)
    verify.assert_not_awaited()
    assert not sessions.data.get("base_activity_rules")
    await controller.handle(query, SimpleNamespace(action="draft_base:save"))
    verify.assert_awaited_once()
    assert sessions.data["base_activity_rules"]["-1002"]["daily_messages"] == 5
    assert "返回准入条件" in str(renderer.rows)


def test_group_bonus_defaults_and_custom_five_messages_use_daily_mode():
    original = draft_data()
    invite = apply_action(original, "draft_bonus_invite_group:-1001")
    assert invite["bonus_working_copy"]["rule"]["invites_per_cycle"] == 1
    editing = apply_action(original, "draft_bonus_activity_group:-1001")
    assert editing["bonus_working_copy"]["rule"]["messages_per_cycle"] == 4
    editing = apply_action(editing, "draft_bonus_input_cycle")
    editing = receive_input(editing, "5")
    text, rows = editor_view(editing, editing["bonus_working_copy"])
    assert "每天发言满 5 条 +1 票" in text
    assert "每天只奖励一次" in text
    assert "北京时间每天 00:00" in text
    assert "自定义 · 5" in [label for row in rows for label, _ in row]
    saved = apply_action(editing, "draft_bonus_activity_save")
    rule = rules_from_data(saved).activity_rule_for(-1001)
    assert rule.daily_cap_mode == "limited" and rule.daily_max_cycles == 1


def test_custom_required_chat_threshold_can_set_four_and_disable_without_changing_other_groups():
    data = {
        "required_chats": [{"id": -1003, "title": "准入群", "type": "supergroup"}],
        "threshold_input_chat": "-1003",
        "chat_message_thresholds": {"-1004": 9},
    }
    saved = apply_threshold_input(data, "4")
    assert saved["chat_message_thresholds"] == {"-1003": 4, "-1004": 9}
    cleared = apply_threshold_input(saved | {"threshold_input_chat": "-1003"}, "0")
    assert cleared["chat_message_thresholds"] == {"-1004": 9}
    assert data["chat_message_thresholds"] == {"-1004": 9}
    with pytest.raises(UserFacingError, match="整数"):
        apply_threshold_input(data, "4.5")


def test_time_text_and_copy_button_share_saved_beijing_time():
    saved = "2026-10-01T12:30:00+00:00"
    prompt = custom_draw_prompt("Europe/London", saved=saved)
    rows = custom_draw_rows("Europe/London", saved=saved)
    assert "<code>2026-10-01 20:30</code>" in prompt
    assert rows[0][0].copy_text.text == "2026-10-01 20:30"
    assert rows[0][0].text == "复制当前时间"
    assert rows[-1] == [("↩ 返回开奖方式", "draft_draw")]
    assert rules_from_data(draft_data() | {"timezone": "Europe/London"}).timezone == "Asia/Shanghai"


async def test_fixed_draw_opens_input_immediately_and_renderer_preserves_code_entity():
    data = draft_data() | {"draw_at": "2026-10-01T20:30:00+08:00"}
    sessions = ThresholdSessions(data)
    renderer = SimpleNamespace(render=AsyncMock())
    settings = CreatorSettings(
        sessions=sessions, renderer=renderer, profiles=object(), queries=object(), bot=object()
    )
    await settings.handle_grant(picker_query(), SimpleNamespace(action="draft_draw_fixed"))
    assert sessions.state == "draft:draw"
    values = renderer.render.await_args.kwargs
    assert values["parse_mode"] == "HTML"
    assert values["rows"][0][0].copy_text.text == "2026-10-01 20:30"
    panel = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2, edit_text=AsyncMock())
    live_renderer = CreatorRenderer(
        bot=object(), tokens=object(), queries=object(), sessions=object()
    )
    live_renderer._keyboard = AsyncMock(return_value=object())
    await live_renderer.render(panel, **values)
    assert panel.edit_text.await_args.kwargs["parse_mode"] == "HTML"
    assert "<code>2026-10-01 20:30</code>" in panel.edit_text.await_args.args[0]


def test_blank_time_uses_future_example_in_beijing():
    text = custom_draw_prompt("UTC", now=datetime(2026, 9, 20, 16, tzinfo=UTC))
    assert "<code>2026-09-22 20:00</code>" in text
