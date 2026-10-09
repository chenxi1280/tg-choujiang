from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import EditMessageText

from tests.test_creator_leaf_experience import _message
from tests.test_creator_numeric_recovery import creator_for
from tests.test_creator_settings import ThresholdSessions
from tests.v22_bonus_test_helpers import draft_data, picker_query
from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_input_replay import recover_applied_input
from tg_lottery.telegram.creator_prizes import CreatorPrizes
from tg_lottery.telegram.creator_renderer import CreatorRenderer, PanelTarget
from tg_lottery.telegram.creator_resume_views import has_field_input, resumed_view
from tg_lottery.telegram.creator_suspension import CreatorSuspension, suspend_draft
from tg_lottery.telegram.draft_config import new_draft


def setting_data():
    return (
        new_draft()
        | draft_data()
        | {
            "panel_message_id": 9,
            "pending_prize_name": "未提交的奖品",
            "prizes": [{"name": "第一奖", "winner_count": 1}],
        }
    )


@pytest.mark.parametrize(
    "state",
    [
        "draft:title",
        "draft:prize_name",
        "draft:prize_count",
        "draft:prize_delete",
        "draft:draw",
        "draft:cover",
        "draft:claim",
        "draft:regions",
        "draft:description",
        "draft:issuer",
        "draft:group_search",
        "draft:admission_search",
        "draft:folder_link",
        "draft:chat_threshold",
        "draft:participation_delete",
    ],
)
async def test_current_stale_setting_button_keeps_original_input_state(state):
    data = setting_data()
    creator, sessions, renderer = creator_for(data, state)
    assert await creator.recover_expired_panel(picker_query())
    assert sessions.state == state
    assert sessions.data["pending_prize_name"] == data["pending_prize_name"]
    assert sessions.data["prizes"] == data["prizes"]
    assert "旧按钮操作未执行" in renderer.edit.await_args.kwargs["text"]
    assert renderer.edit.await_args.kwargs["rows"]
    assert has_field_input(state, sessions.data)


@pytest.mark.parametrize("state", ["draft:prize_delete_confirm", "draft:prize_clear_confirm"])
async def test_current_stale_prize_confirmation_remains_confirmation(state):
    data = setting_data() | {"pending_prize_delete": [1]}
    creator, sessions, renderer = creator_for(data, state)
    assert await creator.recover_expired_panel(picker_query())
    assert sessions.state == state and sessions.data["prizes"] == data["prizes"]
    actual = renderer.edit.await_args.kwargs
    actions = [action for row in actual["rows"] for _, action in row]
    expected = (
        "draft_delete_prize_confirm"
        if state.endswith("delete_confirm")
        else "draft_clear_prizes_confirm"
    )
    assert expected in actions
    assert "确认" in actual["text"]


def test_replayed_participation_input_restores_saved_list_and_navigation():
    data = setting_data() | {"folder_link": "https://t.me/addlist/test_123"}
    updated, state, (text, rows) = resumed_view(data, state="draft:participation", module=False)
    assert state == "draft:participation" and updated == data
    assert "已设置" in text and "组合分享" in text
    assert any(action == "draft_participation_add" for row in rows for _, action in row)


def test_stale_publish_confirmation_does_not_drop_or_execute_confirmation():
    data = setting_data()
    updated, state, (text, rows) = resumed_view(data, state="draft:publish_confirm", module=False)
    assert state == "draft:publish_confirm" and updated == data
    assert "发布前确认" in text
    assert any(action == "draft_publish_confirm" for row in rows for _, action in row)
    assert not updated.get("published_campaign_id")


async def test_delete_input_retry_restores_confirmation_without_deleting_prizes(session_factory):
    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    data = setting_data()
    await sessions.set(201, "draft:prize_delete", data)
    renderer = SimpleNamespace(
        edit=AsyncMock(
            side_effect=[
                TelegramNetworkError(method=EditMessageText(text="确认"), message="reset"),
                None,
            ]
        )
    )
    creator = object.__new__(CampaignCreator)
    creator._sessions, creator._renderer = sessions, renderer
    creator._prizes = CreatorPrizes(sessions=sessions, renderer=renderer)
    message = _message().model_copy(update={"text": "1"})
    with bind_update_context(123, 210):
        with pytest.raises(TelegramNetworkError):
            await creator.handle_message(message)
        assert await creator.handle_message(message)
    stored = await sessions.get(201)
    assert stored.state == "draft:prize_delete_confirm"
    assert stored.data["prizes"] == data["prizes"]
    assert stored.data["pending_prize_delete"] == [1]
    assert "确认删除奖品" in renderer.edit.await_args.kwargs["text"]


@pytest.mark.parametrize("missing", [None, {}, {"content_pages": []}])
async def test_expired_or_invalid_content_page_has_visible_error(missing):
    session = None if missing is None else SimpleNamespace(state="draft:idle", data=missing)
    renderer = object.__new__(CreatorRenderer)
    renderer._sessions = SimpleNamespace(get=AsyncMock(return_value=session))
    with pytest.raises(UserFacingError, match=r"失效|过期"):
        await renderer.page(picker_query(), SimpleNamespace(action="draft_content_page:0"))


async def test_draft_expiring_during_long_panel_render_has_visible_error():
    renderer = object.__new__(CreatorRenderer)
    renderer._sessions = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    with pytest.raises(UserFacingError, match="过期"):
        await renderer._paginate(PanelTarget(201, 201, 9), text="长" * 5000, rows=[])


@pytest.mark.parametrize("state", ["draft:groups", "draft:admission_picker"])
async def test_search_input_retry_restores_same_list_with_saved_selection(state):
    data = setting_data() | {
        "mutation_source_update_id": 210,
        "group_search": "A",
        "group_page": 1,
        "admission_search": "B",
        "admission_page": 1,
    }
    renderer = SimpleNamespace(restore_list=AsyncMock(), edit=AsyncMock())
    with bind_update_context(123, 210):
        assert await recover_applied_input(
            renderer, _message(), SimpleNamespace(state=state, data=data)
        )
    assert renderer.restore_list.await_args.kwargs["state"] == state
    assert renderer.restore_list.await_args.kwargs["data"] == data
    renderer.edit.assert_not_awaited()


async def test_actual_group_list_restore_passes_current_search_page_and_selection():
    data = setting_data() | {"group_search": "A", "group_page": 2}
    renderer = object.__new__(CreatorRenderer)
    renderer._sessions = ThresholdSessions(data)
    renderer.edit_groups = AsyncMock()
    await renderer.restore_list(
        PanelTarget(201, 201, 9), state="draft:groups", data=data, notice="恢复"
    )
    assert renderer._sessions.state == "draft:groups"
    actual = renderer.edit_groups.await_args.kwargs
    assert actual["search"] == "A" and actual["page"] == 2
    assert actual["selected_chat_ids"] == (-1001, -1002)


async def test_actual_admission_list_restore_keeps_search_and_does_not_toggle_selection():
    data = setting_data() | {
        "admission_search": "公告",
        "required_chats": [{"id": -1003, "title": "公告频道", "type": "channel"}],
    }
    renderer = object.__new__(CreatorRenderer)
    renderer._sessions = ThresholdSessions(data)
    renderer._queries = SimpleNamespace(admin_groups=AsyncMock(return_value=[]))
    renderer.edit = AsyncMock()
    await renderer.restore_list(
        PanelTarget(201, 201, 9), state="draft:admission_picker", data=data, notice="恢复"
    )
    assert renderer._sessions.state == "draft:admission_picker"
    assert renderer._sessions.data["required_chats"] == data["required_chats"]
    actual = renderer.edit.await_args.kwargs
    assert "公告频道" in actual["text"] and "搜索\uff1a公告" in actual["text"]
    assert any("✅" in label for row in actual["rows"] for label, _ in row)


@pytest.mark.parametrize("state", ["draft:groups", "draft:admission_picker"])
async def test_resuming_suspended_list_restores_the_live_panel_binding(state):
    data = setting_data() | {"suspended_state": state}
    sessions = ThresholdSessions(data)
    sessions.state = "draft:suspended"
    renderer = SimpleNamespace(restore_list=AsyncMock(), render=AsyncMock())
    suspension = CreatorSuspension(sessions=sessions, renderer=renderer)
    await suspension.resume(picker_query(), SimpleNamespace(action="draft_resume_module"))
    actual = renderer.restore_list.await_args
    assert actual.args[0].message_id == 9 and actual.kwargs["state"] == state
    assert "suspended_state" not in actual.kwargs["data"]
    renderer.render.assert_not_awaited()


@pytest.mark.parametrize(
    "case",
    [
        ("draft:groups", "draft_resume_module", "draft:groups"),
        ("draft:admission_picker", "draft_resume_module", "draft:admission_picker"),
        ("draft:group_search", "draft_resume_module", "draft:groups"),
        ("draft:admission_search", "draft_resume_module", "draft:admission_picker"),
        ("draft:title", "draft_resume_input", "draft:title"),
    ],
)
async def test_resume_retry_after_saved_state_and_failed_display_preserves_step(
    session_factory, case
):
    state, action, expected = case
    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    data = setting_data() | {"group_search": "A", "admission_search": "B"}
    await sessions.set(201, state, data)
    await suspend_draft(sessions, 201)
    renderer = object.__new__(CreatorRenderer)
    renderer._sessions = sessions
    failed_edit = AsyncMock(
        side_effect=[
            TelegramNetworkError(method=EditMessageText(text="恢复"), message="reset"),
            None,
        ]
    )
    renderer.edit_groups = failed_edit
    renderer.render = failed_edit
    renderer.edit = failed_edit
    renderer._queries = SimpleNamespace(admin_groups=AsyncMock(return_value=[]))
    suspension = CreatorSuspension(sessions=sessions, renderer=renderer)
    grant = SimpleNamespace(action=action)
    with bind_update_context(123, 211):
        with pytest.raises(TelegramNetworkError):
            await suspension.resume(picker_query(), grant)
        assert (await sessions.get(201)).state == expected
        await suspension.resume(picker_query(), grant)
    stored = await sessions.get(201)
    assert stored.state == expected
    assert stored.data["draft_id"] == data["draft_id"]
    assert stored.data["participation_group_ids"] == data["participation_group_ids"]
    assert stored.data["group_search"] == "A" and stored.data["admission_search"] == "B"
    assert "suspended_state" not in stored.data
    if expected == "draft:groups":
        assert failed_edit.await_args.kwargs["search"] == "A"
    elif expected == "draft:admission_picker":
        assert "搜索\uff1aB" in failed_edit.await_args.kwargs["text"]
    else:
        assert "已恢复输入\uff1a活动标题" in failed_edit.await_args.kwargs["text"]


async def test_stale_group_removal_confirmation_preserves_rules_and_requires_confirmation():
    data = setting_data() | {
        "pending_group_removal_ids": [-1001],
        "pending_group_removal_titles": ["A群"],
    }
    creator, sessions, renderer = creator_for(data, "draft:group_remove_confirm")
    assert await creator.recover_expired_panel(picker_query())
    assert sessions.state == "draft:group_remove_confirm"
    assert sessions.data["participation_group_ids"] == data["participation_group_ids"]
    actual = renderer.edit.await_args.kwargs
    assert "A群" in actual["text"]
    assert any(
        action == "draft_group_remove_confirm" for row in actual["rows"] for _, action in row
    )
