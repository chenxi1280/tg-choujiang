from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from tests.test_creator_gate_feedback import _input_data, _message
from tests.test_creator_settings import ThresholdSessions
from tests.v30_helpers import FIRST
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram import creator_behavior_views as views
from tg_lottery.telegram.creator_behavior_admission import CreatorBehaviorAdmission
from tg_lottery.telegram.creator_gate_quick_save import prepare_save


@pytest.fixture
def setup(monkeypatch):
    permissions = AsyncMock()
    monkeypatch.setattr(
        "tg_lottery.telegram.creator_behavior_admission.verify_publication_bonus_permissions",
        permissions,
    )
    reply = AsyncMock(return_value=_message().model_copy(update={"message_id": 401}))
    monkeypatch.setattr(Message, "answer", reply)
    return permissions, reply


def controller(data):
    sessions = ThresholdSessions(data)
    sessions.state = "draft:behavior_admission"
    renderer = SimpleNamespace(render=AsyncMock(), edit=AsyncMock())
    return (
        CreatorBehaviorAdmission(sessions=sessions, renderer=renderer, bot=object()),
        sessions,
        renderer,
    )


@pytest.mark.parametrize(
    "mode,field", [("message", "daily_messages"), ("invite", "required_valid_invites")]
)
@pytest.mark.parametrize("value", ["5", "8", "10", "34", "\uff11\uff12"])
async def test_reply_saves_current_group_and_replies_at_newest_message(
    setup, mode, field, *, value
):
    data = _input_data(mode, field)
    admission, sessions, renderer = controller(data)
    await admission.receive(_message(value), data)
    assert sessions.data["admission"]["groups"][str(FIRST)][field] == int(value)
    assert sessions.data["panel_message_id"] == 401
    assert sessions.data[editor.EDITOR_KEY]["field"] is None
    assert "已保存本群" in renderer.render.await_args.kwargs["text"]
    assert renderer.render.await_args.args[0].message_id == 401
    assert "已保存本群" in setup[1].await_args.args[0]
    setup[0].assert_awaited_once()


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "²", "9" * 5000, "1_0", ""])
async def test_invalid_input_keeps_field_and_last_saved_rule_with_actionable_latest_reply(
    setup, value
):
    data = _input_data()
    before = deepcopy(data)
    admission, sessions, renderer = controller(data)
    await admission.receive(_message(value), data)
    assert sessions.data["admission"] == before["admission"]
    assert sessions.data[editor.EDITOR_KEY]["rule"] == before[editor.EDITOR_KEY]["rule"]
    assert sessions.data[editor.EDITOR_KEY]["field"] == "daily_messages"
    values = renderer.render.await_args.kwargs
    assert "未保存" in values["text"] and "正整数" in values["text"]
    assert ("8条/天", "draft_gate:quick:daily_messages:8") in [
        b for row in values["rows"] for b in row
    ]
    setup[0].assert_not_awaited()


async def test_direct_reply_without_custom_button_uses_current_group_count(setup):
    data = _input_data()
    data[editor.EDITOR_KEY]["field"] = None
    admission, sessions, _ = controller(data)
    await admission.receive(_message("8"), data)
    assert sessions.data["admission"]["groups"][str(FIRST)]["daily_messages"] == 8
    assert sessions.data["admission"]["groups"][str(FIRST)]["consecutive_days"] == 1


async def test_error_then_valid_reply_saves_without_second_submit(setup):
    admission, sessions, renderer = controller(_input_data())
    await admission.receive(_message("0"), sessions.data)
    await admission.receive(_message("10"), sessions.data)
    assert sessions.data["admission"]["groups"][str(FIRST)]["daily_messages"] == 10
    assert "未保存" not in renderer.render.await_args.kwargs["text"]
    assert setup[1].await_count == 2


async def test_permission_failure_is_not_saved_and_can_retry(setup):
    data = _input_data()
    before = deepcopy(data)
    setup[0].side_effect = UserFacingError("管理员权限已失效")
    admission, sessions, renderer = controller(data)
    await admission.receive(_message("8"), data)
    assert sessions.data["admission"] == before["admission"]
    assert sessions.data[editor.EDITOR_KEY]["rule"] == before[editor.EDITOR_KEY]["rule"]
    assert "未保存" in renderer.render.await_args.kwargs["text"]
    setup[0].side_effect = None
    await admission.receive(_message("8"), sessions.data)
    assert sessions.data["admission"]["groups"][str(FIRST)]["daily_messages"] == 8


def test_save_current_group_never_commits_other_staged_groups():
    data = _input_data()
    data["participation_groups"].append({"id": -1002, "title": "B群"})
    data["admission"] = {
        "type": "message",
        "groups": {"-1002": {"daily_messages": 5, "consecutive_days": 1}},
    }
    data[editor.EDITOR_KEY]["groups"]["-1002"] = {"daily_messages": 99, "consecutive_days": 3}
    changed = prepare_save(data, field="daily_messages", value="8")
    assert changed["admission"]["groups"]["-1002"]["daily_messages"] == 5
    assert changed[editor.EDITOR_KEY]["groups"]["-1002"]["daily_messages"] == 99
    assert changed["admission"]["groups"][str(FIRST)]["daily_messages"] == 8
    assert data["admission"]["groups"].keys() == {"-1002"}


async def test_quick_callback_and_explicit_save_use_real_permissions(setup):
    admission, sessions, renderer = controller(_input_data())
    query = SimpleNamespace(from_user=_message().from_user, message=_message())
    await admission.handle(query, SimpleNamespace(action="draft_gate:quick:daily_messages:8"))
    assert sessions.data["admission"]["groups"][str(FIRST)]["daily_messages"] == 8
    await admission.handle(query, SimpleNamespace(action="draft_gate:save_group"))
    assert setup[0].await_count == 2
    assert "已保存本群" in renderer.render.await_args.kwargs["text"]


def test_no_target_copy_and_pending_confirmation_cannot_quick_save():
    for extra in (
        {"chat_id": None},
        {"copy_targets": []},
        {"pending_action": "draft_gate:mode:invite"},
    ):
        data = _input_data()
        data[editor.EDITOR_KEY] |= extra
        with pytest.raises(UserFacingError):
            prepare_save(data, field="daily_messages", value="8")


def test_days_are_separate_and_clean_saved_gate_has_no_second_save_requirement():
    data = prepare_save(_input_data(field="consecutive_days"), field="consecutive_days", value="3")
    assert data["admission"]["groups"][str(FIRST)]["daily_messages"] == 5
    assert data["admission"]["groups"][str(FIRST)]["consecutive_days"] == 3
    returned = editor.apply_action(data, "draft_gate:list")
    _, rows = views.view(returned)
    assert "draft_gate:save" not in [a for row in rows for _, a in row]
    assert ("返回预览\uff08已保存\uff09", "draft_gate:leave") in [b for row in rows for b in row]


async def test_committed_reply_display_failure_replays_saved_result_without_second_save(
    session_factory, setup
):
    from tg_lottery.application.admin_sessions import AdminSessionService
    from tg_lottery.application.request_context import bind_update_context
    from tg_lottery.infrastructure.db.uow import UnitOfWork
    from tg_lottery.telegram.campaign_creator import CampaignCreator

    sessions = AdminSessionService(lambda: UnitOfWork(session_factory))
    data = _input_data()
    await sessions.set(101, "draft:behavior_admission", data)
    renderer = SimpleNamespace(
        render=AsyncMock(side_effect=RuntimeError("display failed")), edit=AsyncMock()
    )
    admission = CreatorBehaviorAdmission(sessions=sessions, renderer=renderer, bot=object())
    message = _message("8")
    with bind_update_context(999, 700):
        with pytest.raises(RuntimeError, match="display failed"):
            await admission.receive(message, data)
        creator = object.__new__(CampaignCreator)
        creator._sessions, creator._renderer = sessions, renderer
        assert await creator.handle_message(message)
    saved = await sessions.get(101)
    assert saved.data["admission"]["groups"][str(FIRST)]["daily_messages"] == 8
    assert saved.data["panel_message_id"] == 401
    assert "已保存本群" in renderer.edit.await_args.kwargs["text"]
    assert setup[0].await_count == setup[1].await_count == 1


async def test_time_window_rejection_preserves_original_saved_gate(setup, monkeypatch):
    def reject(_):
        raise UserFacingError("连续天数无法在截止前完成")

    monkeypatch.setattr("tg_lottery.telegram.creator_behavior_admission.validate_gate_time", reject)
    data = _input_data()
    admission, sessions, renderer = controller(data)
    await admission.receive(_message("8"), data)
    assert sessions.data["admission"] is None
    assert "未保存" in renderer.render.await_args.kwargs["text"]
    setup[0].assert_not_awaited()


async def test_reply_to_old_panel_cannot_change_new_group_or_field(setup):
    data = _input_data()
    admission, sessions, renderer = controller(data)
    old = _message().model_copy(update={"message_id": 50})
    message = _message("8").model_copy(update={"reply_to_message": old})
    await admission.receive(message, data)
    assert sessions.data["admission"] is None
    assert "所回复的门槛页已更新" in renderer.render.await_args.kwargs["text"]
    setup[0].assert_not_awaited()


def test_removed_group_and_retention_conflict_are_rejected_before_save():
    data = _input_data()
    with pytest.raises(UserFacingError, match="已移除"):
        prepare_save(data | {"participation_groups": []}, field="daily_messages", value="8")
    data["invite_retention_hours"] = 48
    with pytest.raises(UserFacingError, match="24小时"):
        prepare_save(data, field="daily_messages", value="8")


async def test_long_group_list_error_reply_uses_safe_first_page_and_keeps_full_view(setup):
    data = _input_data()
    groups = [{"id": -1000 - i, "title": "长名称测试群" * 30 + str(i)} for i in range(30)]
    data["participation_groups"] = groups
    data[editor.EDITOR_KEY] |= {
        "chat_id": None,
        "field": None,
        "groups": {str(g["id"]): {"daily_messages": 5, "consecutive_days": 1} for g in groups},
    }
    admission, sessions, renderer = controller(data)
    await admission.receive(_message("0"), data)
    text = setup[1].await_args.args[0]
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert "未保存" in text
    assert len(renderer.render.await_args.kwargs["text"].encode("utf-16-le")) // 2 > 4096
    assert sessions.data["admission"] is None
    setup[0].assert_not_awaited()
