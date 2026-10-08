from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_group_bonus
from tg_lottery.telegram.creator_bonus_activation import activation_notice, save_label
from tg_lottery.telegram.creator_prizes import CreatorPrizes
from tg_lottery.telegram.creator_sections import enter_section, next_draft_action, section_view
from tg_lottery.telegram.creator_views import dashboard_rows
from tg_lottery.telegram.draft_config import new_draft
from tg_lottery.telegram.home_views import home_keyboard
from tg_lottery.telegram.personal_flow import PersonalFlow
from tg_lottery.telegram.ui_style import plain_label


@pytest.mark.parametrize(
    "data,label",
    [
        (None, "创建抽奖"),
        ({"title": "继续本稿"}, "继续创建抽奖"),
        ({"published_campaign_id": "existing"}, "查看发布状态"),
    ],
)
def test_home_has_one_creation_entry_and_separate_user_roles(data, label):
    rows = home_keyboard(data).inline_keyboard
    assert [plain_label(button.text) for button in rows[0]] == ["我参加的抽奖", "我发起的抽奖"]
    assert plain_label(rows[1][0].text) == label
    assert sum(len(row) for row in rows) == 5
    assert {button.callback_data for row in rows for button in row} >= {
        "nav:join",
        "nav:created",
        "nav:settings",
        "nav:help",
    }


def test_creator_next_action_reaches_missing_gate_without_duplicate_bonus_entries():
    data = new_draft() | {
        "participation_groups": [{"id": -1001, "title": "A群"}],
        "title": "本场",
        "prizes": [{"name": "奖品", "winner_count": 1}],
    }
    rows = dashboard_rows(data)
    assert sum(len(row) for row in rows) == 10
    assert next_draft_action(data) == ("设置抽奖门槛", "draft_gate")
    actions = [action for row in rows for _, action in row]
    assert actions.count("draft_rules") == 1
    assert "draft_bonus_activity" not in actions
    assert "draft_bonus_invite" not in actions


@pytest.mark.parametrize("section", ["prizes", "cover"])
def test_combined_entry_does_not_start_a_field_input(section):
    text, rows = section_view(new_draft(), section)
    assert "请回复" not in text
    assert rows[-1] == [("返回草稿", "draft_back")]


def test_section_ancestor_return_does_not_build_a_navigation_loop():
    original = enter_section(new_draft(), "conditions")
    child = enter_section(original, "user_limits")
    returned = enter_section(child, "conditions")
    assert returned["creator_path"] == ["dashboard", "conditions"]
    assert child["creator_path"] == ["dashboard", "conditions", "user_limits"]


def test_saving_a_disabled_bonus_declares_all_restored_groups_and_preserves_other_module():
    data = new_draft() | {
        "bonus_types": ["invite"],
        "participation_groups": [{"id": -1001, "title": "A群"}, {"id": -1002, "title": "B群"}],
        "group_activity_rules": {"-1002": {"enabled": True}},
    }
    assert save_label(data, "activity") == "保存并开启本加成"
    notice = activation_notice(data, "activity", chat_id=-1001)
    assert "A群" in notice and "B群" in notice
    original = deepcopy(data)
    opened = creator_group_bonus.apply_action(data, "draft_bonus_activity_group:-1001")
    saved = creator_group_bonus.apply_action(opened, "draft_bonus_activity_save")
    assert saved["bonus_types"] == ["invite", "activity"]
    assert saved["group_activity_rules"]["-1002"] == original["group_activity_rules"]["-1002"]
    assert data == original


def _prize_fixture(data):
    sessions = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(state="draft:idle", data=data)), set=AsyncMock()
    )
    renderer = SimpleNamespace(edit=AsyncMock(), render=AsyncMock())
    flow = CreatorPrizes(sessions=sessions, renderer=renderer)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=201), chat=SimpleNamespace(id=201), text="", message_id=10
    )
    return flow, sessions, message


async def test_prize_name_is_pending_until_a_valid_count_and_count_failure_keeps_it():
    data = new_draft() | {"panel_message_id": 10, "prizes": [{"name": "原奖", "winner_count": 1}]}
    flow, sessions, message = _prize_fixture(data)
    message.text = "新增奖"
    await flow.receive_name(message, data)
    pending = sessions.set.await_args.args[2]
    assert pending["pending_prize_name"] == "新增奖"
    assert pending["prizes"] == data["prizes"]
    sessions.set.reset_mock()
    message.text = "不是人数"
    with pytest.raises(UserFacingError, match="正整数"):
        await flow.receive_count(message, pending)
    sessions.set.assert_not_awaited()
    message.text = "2"
    await flow.receive_count(message, pending)
    saved = sessions.set.await_args.args[2]
    assert saved["prizes"][-1] == {"name": "新增奖", "winner_count": 2}
    assert "pending_prize_name" not in saved


async def test_personal_home_callback_updates_existing_panel_and_uses_real_draft_state():
    queries = SimpleNamespace(
        creator_navigation_state=AsyncMock(return_value={"title": "继续本稿"})
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=201), answer=AsyncMock(), edit_text=AsyncMock()
    )
    flow = PersonalFlow(profiles=None, queries=queries, bot_username="test_bot")
    await flow.show_index(message, edit=True)
    queries.creator_navigation_state.assert_awaited_once_with(201)
    message.answer.assert_not_awaited()
    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    assert plain_label(keyboard.inline_keyboard[1][0].text) == "继续创建抽奖"
