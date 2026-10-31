from types import SimpleNamespace

import pytest

from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.v22_bonus_test_helpers import draft_data, picker_query
from tg_lottery.telegram import creator_group_bonus as bonus
from tg_lottery.telegram.creator_bonus import bonus_summary
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.draft_config import new_draft


@pytest.mark.parametrize("kind", ["invite", "activity"])
async def test_cancel_custom_input_preserves_prior_group_changes(kind):
    data = bonus.apply_action(draft_data(), f"draft_bonus_{kind}_group:-1001")
    data = bonus.apply_action(data, "draft_bonus_preset:cycle:10")
    sessions = ThresholdSessions(data)
    renderer = RecordingRenderer()
    settings = CreatorSettings(
        sessions=sessions, renderer=renderer, profiles=object(), queries=object(), bot=object()
    )
    query = picker_query()
    await settings.handle_grant(query, SimpleNamespace(action="draft_bonus_input_cycle"))
    cancel = next(action for row in renderer.rows for label, action in row if label == "取消输入")

    await settings.handle_grant(query, SimpleNamespace(action=cancel))

    assert sessions.state == "draft:idle"
    assert sessions.data["bonus_working_copy"] == data["bonus_working_copy"]
    assert not sessions.data.get("bonus_input_field")
    unit = "人" if kind == "invite" else "条"
    assert f"自定义 · 10{unit}" in str(renderer.rows)
    assert not sessions.data[f"group_{kind}_rules"]


@pytest.mark.parametrize("kind", ["invite", "activity"])
def test_empty_bonus_list_offers_group_selection(kind):
    data = draft_data() | {
        "participation_groups": [],
        "participation_group_ids": [],
        "chat_id": None,
    }
    text, rows = bonus.list_view(data, kind)
    assert "请先选择参与活动的群" in text
    assert any(("选择参与活动的群", "draft_group") in row for row in rows)


def test_custom_daily_cap_is_preserved_when_reselecting_limited_mode():
    data = bonus.apply_action(draft_data(), "draft_bonus_activity_group:-1001")
    data = bonus.apply_action(data, "draft_bonus_input_cap")
    data = bonus.receive_input(data, "7")
    data = bonus.apply_action(data, "draft_bonus_activity_limited")
    assert data["bonus_working_copy"]["rule"]["daily_max_cycles"] == 7


def test_changing_message_count_does_not_change_remembered_daily_cap():
    data = bonus.apply_action(draft_data(), "draft_bonus_activity_group:-1001")
    data = bonus.apply_action(data, "draft_bonus_preset:cap:7")
    data = bonus.apply_action(data, "draft_bonus_activity_unlimited")
    data = bonus.apply_action(data, "draft_bonus_activity_cycle_inc")
    data = bonus.apply_action(data, "draft_bonus_activity_limited")
    assert data["bonus_working_copy"]["rule"]["daily_max_cycles"] == 7


@pytest.mark.parametrize("kind,label", [("invite", "邀请加成票"), ("activity", "抽奖加成票(发言)")])
def test_new_bonus_without_groups_shows_pending_configuration(kind, label):
    summary = bonus_summary(new_draft() | {"bonus_types": [kind]})
    assert f"{label}待逐群设置" in summary
    assert "None" not in summary
