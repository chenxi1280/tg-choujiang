from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.v22_bonus_test_helpers import picker_query
from tests.v30_helpers import FIRST, SECOND
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram import creator_behavior_views as views
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_behavior_admission import CreatorBehaviorAdmission
from tg_lottery.telegram.draft_config import new_draft


def gate_data(*, multiple=True):
    groups = [{"id": FIRST, "title": "A群"}]
    if multiple:
        groups.append({"id": SECOND, "title": "B群"})
    data = editor.open_editor(new_draft() | {"participation_groups": groups})
    data = editor.apply_action(data, "draft_gate:mode:message")
    return editor.apply_action(data, f"draft_gate:group:{FIRST}")


def actions(rows):
    return {action for row in rows for _, action in row}


def test_opening_default_group_can_return_without_unsaved_warning():
    data = gate_data()
    assert not editor.group_dirty(data)
    returned = editor.apply_action(data, "draft_gate:list")
    assert not returned[editor.EDITOR_KEY].get("pending_action")
    assert returned[editor.EDITOR_KEY]["groups"] == {}
    assert returned["admission"] is None
    assert "draft_gate:group:" + str(FIRST) in actions(views.view(returned)[1])


@pytest.mark.parametrize("destination", ["draft_gate:list", f"draft_gate:group:{SECOND}"])
def test_modified_group_can_be_staged_before_navigation_without_committing_whole_gate(destination):
    data = editor.apply_action(gate_data(), "draft_gate:preset:daily_messages:20")
    original = deepcopy(data)
    pending = editor.apply_action(data, destination)
    assert "draft_gate:save_and_continue" in actions(views.view(pending)[1])
    continued = editor.apply_action(pending, "draft_gate:save_and_continue")
    assert continued[editor.EDITOR_KEY]["groups"][str(FIRST)]["daily_messages"] == 20
    assert continued["admission"] is None
    assert not continued[editor.EDITOR_KEY].get("pending_action")
    expected_group = str(SECOND) if destination.endswith(str(SECOND)) else None
    assert continued[editor.EDITOR_KEY]["chat_id"] == expected_group
    assert data == original


def test_mode_change_never_offers_misleading_save_then_discard():
    changed = editor.apply_action(gate_data(), "draft_gate:preset:daily_messages:20")
    pending = editor.apply_action(changed, "draft_gate:mode:invite")
    assert "draft_gate:save_and_continue" not in actions(views.view(pending)[1])
    kept = editor.apply_action(pending, "draft_gate:keep_editing")
    assert kept[editor.EDITOR_KEY]["rule"]["daily_messages"] == 20
    assert kept[editor.EDITOR_KEY]["type"] == "message"


def test_single_group_has_no_dead_end_copy_button():
    data = gate_data(multiple=False)
    assert "draft_gate:copy_open" not in actions(views.view(data)[1])
    copied = editor.apply_action(data, "draft_gate:copy_open")
    text, rows = views.view(copied)
    assert "没有其他参与群可复制" in text
    assert "draft_gate:copy_confirm" not in actions(rows)
    assert "draft_gate:copy_cancel" in actions(rows)


def test_copy_confirmation_appears_only_after_target_selection():
    data = editor.apply_action(gate_data(), "draft_gate:copy_open")
    assert "draft_gate:copy_confirm" not in actions(views.view(data)[1])
    with pytest.raises(UserFacingError, match="请先选择"):
        editor.apply_action(data, "draft_gate:copy_confirm")
    selected = editor.toggle_copy(data, str(SECOND))
    assert "draft_gate:copy_confirm" in actions(views.view(selected)[1])
    assert FIRST not in selected[editor.EDITOR_KEY]["groups"]
    confirmed = editor.apply_action(selected, "draft_gate:copy_confirm")
    assert set(confirmed[editor.EDITOR_KEY]["groups"]) == {str(SECOND)}


def test_normalized_saved_defaults_do_not_create_dirty_warning():
    data = new_draft() | {
        "participation_groups": [{"id": FIRST, "title": "A群"}],
        "admission": {"type": "message", "groups": {str(FIRST): {"daily_messages": 5}}},
    }
    opened = editor.apply_action(editor.open_editor(data), f"draft_gate:group:{FIRST}")
    unchanged = editor.apply_action(opened, "draft_gate:preset:consecutive_days:1")
    assert not editor.group_dirty(unchanged)
    assert not editor.apply_action(unchanged, "draft_gate:list")[editor.EDITOR_KEY].get(
        "pending_action"
    )


async def test_leave_staged_gate_offers_save_and_returns_without_losing_parameters(monkeypatch):
    data = editor.apply_action(gate_data(), "draft_gate:preset:daily_messages:20")
    data = editor.apply_action(data, "draft_gate:group_save")
    sessions = ThresholdSessions(data)
    renderer = RecordingRenderer()
    controller = CreatorBehaviorAdmission(sessions=sessions, renderer=renderer, bot=object())
    assert not await controller.handle(picker_query(), SimpleNamespace(action="draft_gate:leave"))
    assert "draft_gate:save" in actions(renderer.rows)
    assert sessions.data["admission"] is None
    permission_check = AsyncMock()
    monkeypatch.setattr(
        "tg_lottery.telegram.creator_behavior_admission.verify_publication_bonus_permissions",
        permission_check,
    )
    assert await controller.handle(picker_query(), SimpleNamespace(action="draft_gate:save"))
    assert sessions.data["admission"]["groups"][str(FIRST)]["daily_messages"] == 20
    assert editor.EDITOR_KEY not in sessions.data
    permission_check.assert_awaited_once()


@pytest.mark.parametrize("messages,dirty", [(5, False), (20, True)])
def test_existing_unsubmitted_editor_without_baseline_preserves_real_changes(messages, dirty):
    data = editor.apply_action(gate_data(), f"draft_gate:preset:daily_messages:{messages}")
    data[editor.EDITOR_KEY].pop("rule_baseline")
    assert editor.group_dirty(data) is dirty


async def test_used_button_on_current_gate_panel_restores_gate_without_replaying_action():
    data = editor.apply_action(gate_data(), "draft_gate:preset:daily_messages:20")
    data |= {"panel_message_id": 9}
    sessions = ThresholdSessions(data)
    sessions.state = "draft:behavior_admission"
    renderer = SimpleNamespace(edit=AsyncMock())
    creator = object.__new__(CampaignCreator)
    creator._sessions, creator._renderer = sessions, renderer
    before = deepcopy(data[editor.EDITOR_KEY])
    assert await creator.recover_expired_panel(picker_query())
    assert sessions.state == "draft:behavior_admission"
    assert sessions.data[editor.EDITOR_KEY] == before
    assert sessions.data["admission"] is None
    assert renderer.edit.await_args.kwargs["text"].startswith("旧按钮操作未执行")
    assert "draft_gate:save_group" in actions(renderer.edit.await_args.kwargs["rows"])
