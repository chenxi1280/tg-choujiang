from copy import deepcopy
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_creator_settings import ThresholdSessions
from tests.v22_bonus_test_helpers import draft_data, picker_query
from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.errors import AppliedDraftCallback, UserFacingError
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram import creator_behavior_editor as gate
from tg_lottery.telegram import creator_behavior_views as gate_views
from tg_lottery.telegram import creator_bonus_copy as copy
from tg_lottery.telegram import creator_group_bonus as bonus
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_bonus_fields import positive_integer
from tg_lottery.telegram.creator_resume_views import resumed_view
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.draft_config import new_draft


def editing_data(kind):
    data = new_draft() | draft_data() | {"panel_message_id": 9}
    data = bonus.apply_action(data, f"draft_bonus_{kind}_group:-1001")
    return bonus.apply_action(data, "draft_bonus_preset:cycle:7")


def creator_for(data, state):
    sessions = ThresholdSessions(data)
    sessions.state = state
    renderer = SimpleNamespace(edit=AsyncMock(), render=AsyncMock())
    creator = object.__new__(CampaignCreator)
    creator._sessions, creator._renderer = sessions, renderer
    creator._platform_policy = None
    return creator, sessions, renderer


@pytest.mark.parametrize("kind", ["invite", "activity"])
@pytest.mark.parametrize("waiting", [True, False])
async def test_old_current_bonus_button_preserves_step_and_uncommitted_values(kind, waiting):
    data = editing_data(kind)
    state = "draft:idle"
    if waiting:
        data = bonus.apply_action(data, "draft_bonus_input_cycle")
        state = "draft:bonus_input"
    before = deepcopy(data)
    creator, sessions, renderer = creator_for(data, state)
    assert await creator.recover_expired_panel(picker_query())
    assert sessions.state == state
    assert sessions.data["bonus_working_copy"] == before["bonus_working_copy"]
    assert sessions.data.get("bonus_input_field") == before.get("bonus_input_field")
    assert sessions.data[f"group_{kind}_rules"] == {}
    values = renderer.edit.await_args.kwargs
    actions = [action for row in values["rows"] for _, action in row]
    assert values["text"].startswith("旧按钮操作未执行")
    assert (
        "draft_bonus_input_cancel" in actions if waiting else "draft_bonus_input_cycle" in actions
    )


def copying_data(kind, confirming):
    data = editing_data(kind)
    data = bonus.apply_action(data, f"draft_bonus_{kind}_save")
    data = bonus.apply_action(data, f"draft_bonus_{kind}_group:-1001")
    data = copy.apply_action(data, "draft_bonus_copy_start")
    data = copy.apply_action(data, "draft_bonus_copy_toggle:-1002")
    return copy.apply_action(data, "draft_bonus_copy_continue") if confirming else data


@pytest.mark.parametrize("kind", ["invite", "activity"])
@pytest.mark.parametrize("confirming", [True, False])
async def test_old_copy_button_restores_selection_or_confirmation_without_applying(
    kind, confirming
):
    data = copying_data(kind, confirming)
    before = deepcopy(data)
    creator, sessions, renderer = creator_for(data, "draft:idle")
    assert await creator.recover_expired_panel(picker_query())
    assert sessions.data["bonus_copy"] == before["bonus_copy"]
    assert sessions.data[f"group_{kind}_rules"] == before[f"group_{kind}_rules"]
    assert "-1002" not in sessions.data[f"group_{kind}_rules"]
    expected = copy.copy_view(before)
    actual = renderer.edit.await_args.kwargs
    assert actual["rows"] == expected[1]


@pytest.mark.parametrize("kind", ["invite", "activity"])
def test_input_replay_restores_copy_dialog_instead_of_dashboard(kind):
    data = copying_data(kind, True)
    updated, state, view = resumed_view(data, state="draft:idle", module=False)
    assert state == "draft:idle" and updated == data
    assert view == copy.copy_view(data)


@pytest.mark.parametrize("kind", ["invite", "activity"])
def test_explicit_new_group_and_preset_clear_legacy_pending_input(kind):
    data = bonus.apply_action(editing_data(kind), "draft_bonus_input_cycle")
    new_group = bonus.apply_action(data, f"draft_bonus_{kind}_group:-1002")
    assert not new_group.get("bonus_input_field")
    selected = bonus.apply_action(data, "draft_bonus_preset:cycle:10")
    assert not selected.get("bonus_input_field")
    key = "invites_per_cycle" if kind == "invite" else "messages_per_cycle"
    assert selected["bonus_working_copy"]["rule"][key] == 10
    assert data["bonus_working_copy"]["rule"][key] == 7


@pytest.mark.parametrize(
    "value",
    ["²", "9" * 5000, "0", "-1", "1.5", ""],
    ids=["superscript", "too_long", "zero", "negative", "fraction", "empty"],
)
def test_invalid_integer_is_actionable_without_internal_parser_error(value):
    with pytest.raises(UserFacingError, match="正整数"):
        positive_integer(value)


@pytest.mark.parametrize("value,expected", [("3", 3), ("\uff11\uff12", 12), ("\u0660\u0663", 3)])
def test_decimal_integer_remains_supported(value, expected):
    assert positive_integer(value) == expected


@pytest.mark.parametrize(
    "kind,field,label,value",
    [
        ("invite", "cycle", "每轮有效邀请人数", 7),
        ("activity", "cycle", "每轮发言条数", 7),
        ("activity", "reward", "每轮奖励次数", 1),
    ],
)
def test_bonus_input_names_group_field_and_current_value(kind, field, label, *, value):
    data = editing_data(kind)
    if field == "reward":
        data["bonus_policy_version"] = None
    data = bonus.apply_action(data, f"draft_bonus_input_{field}")
    text = bonus.input_prompt(data)
    assert "A群" in text and label in text and str(value) in text
    assert "只发送数字" in text and "保存本群后生效" in text


@pytest.mark.parametrize(
    "mode,field",
    [
        ("message", "daily_messages"),
        ("message", "consecutive_days"),
        ("invite", "required_valid_invites"),
    ],
)
def test_gate_input_names_group_and_separate_save_scope(mode, field):
    data = gate.open_editor(new_draft() | draft_data())
    data = gate.apply_action(data, f"draft_gate:mode:{mode}")
    data = gate.apply_action(data, "draft_gate:group:-1001")
    data = gate.apply_action(data, f"draft_gate:input:{field}")
    text, rows = gate_views.view(data)
    assert "抽奖门槛" in text and "A群" in text and gate_views.FIELD_LABELS[field] in text
    assert "直接保存本群" in text
    assert rows[-1] == [("取消输入", "draft_gate:cancel_input")]
    assert any(action.startswith("draft_gate:quick:") for row in rows for _, action in row)


@pytest.mark.parametrize("kind", ["invite", "activity"])
@pytest.mark.parametrize("value", ["²", "9" * 5000], ids=["superscript", "too_long"])
async def test_invalid_bonus_input_preserves_waiting_state_and_working_values(kind, value):
    data = bonus.apply_action(editing_data(kind), "draft_bonus_input_cycle")
    before = deepcopy(data)
    sessions = ThresholdSessions(data)
    sessions.state = "draft:bonus_input"
    renderer = SimpleNamespace(edit=AsyncMock())
    settings = CreatorSettings(
        sessions=sessions, renderer=renderer, bot=object(), profiles=object(), queries=object()
    )
    query = picker_query()
    message = query.message.model_copy(update={"text": value, "from_user": query.from_user})
    with pytest.raises(UserFacingError, match="正整数"):
        await settings.handle_message(message, sessions.state, deepcopy(data))
    assert sessions.state == "draft:bonus_input" and sessions.data == before
    renderer.edit.assert_not_awaited()


@pytest.mark.parametrize("kind", ["invite", "activity"])
def test_copy_pagination_has_real_directions_and_body_numbers(kind):
    data = copying_data(kind, False)
    data["participation_groups"] += [{"id": -1000 - i, "title": f"其他群{i}"} for i in range(3, 9)]
    text, rows = copy.copy_view(data)
    actions = [action for row in rows for _, action in row]
    assert "第1/2页" in text and "1. " in text
    assert "draft_bonus_copy_noop" not in actions and "draft_bonus_copy_page:prev" not in actions
    assert "draft_bonus_copy_page:next" in actions
    second = copy.apply_action(data, "draft_bonus_copy_page:next")
    text, rows = copy.copy_view(second)
    actions = [action for row in rows for _, action in row]
    assert "第2/2页" in text and "7. " in text
    assert "draft_bonus_copy_page:prev" in actions and "draft_bonus_copy_page:next" not in actions


@pytest.mark.parametrize(
    "action",
    [
        "draft_bonus_copy_confirm",
        "draft_bonus_group_disable",
        "draft_gate:copy_confirm",
        "draft_gate:disable",
        "draft_gate:save_and_continue",
    ],
)
async def test_committed_editor_operations_restore_instead_of_running_again(
    session_factory, action
):
    factory = partial(UnitOfWork, session_factory)
    sessions, tokens = AdminSessionService(factory), CallbackTokenService(factory)
    prepared, state = committable_data(action)
    await sessions.set(201, state, prepared)
    context = await sessions.issue_panel_context(201, 10)
    (token,) = await tokens.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(CallbackRequest(action, payload=context.callback_payload()),),
    )
    with bind_update_context(123, 901):
        first = await tokens.consume(token, owner_user_id=201, chat_id=201)
        current = await sessions.get(201)
        apply = bonus.apply_action if action.startswith("draft_bonus_") else gate.apply_action
        committed = apply(current.data, first.action)
        await sessions.set(201, state, committed)
        with pytest.raises(AppliedDraftCallback, match="already committed"):
            await tokens.consume(token, owner_user_id=201, chat_id=201)
    final = await sessions.get(201)
    key = "group_activity_rules" if action.startswith("draft_bonus_") else "admission_editor"
    assert final.data[key] == committed[key]


def committable_data(action):
    if action.startswith("draft_bonus_"):
        data = editing_data("activity")
        data = bonus.apply_action(data, "draft_bonus_activity_save")
        data = bonus.apply_action(data, "draft_bonus_activity_group:-1001")
        if action == "draft_bonus_copy_confirm":
            data = copy.apply_action(data, "draft_bonus_copy_start")
            data = copy.apply_action(data, "draft_bonus_copy_toggle:-1002")
            data = copy.apply_action(data, "draft_bonus_copy_continue")
        return data, "draft:idle"
    data = gate.open_editor(new_draft() | draft_data())
    data = gate.apply_action(data, "draft_gate:mode:message")
    data = gate.apply_action(data, "draft_gate:group:-1001")
    data = gate.apply_action(data, "draft_gate:preset:daily_messages:7")
    if action == "draft_gate:copy_confirm":
        data = gate.apply_action(data, "draft_gate:copy_open")
        data = gate.toggle_copy(data, "-1002")
    elif action == "draft_gate:disable":
        data = gate.apply_action(data, "draft_gate:group_save")
        data = gate.apply_action(data, "draft_gate:group:-1001")
    else:
        data = gate.apply_action(data, "draft_gate:list")
    return data, "draft:behavior_admission"
