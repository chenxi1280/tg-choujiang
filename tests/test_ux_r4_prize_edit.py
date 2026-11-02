from copy import deepcopy
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_creator_leaf_experience import _message as real_message
from tests.test_v32_creator_controls import _prize_fixture
from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.domain.errors import RecoverableDraftCallback, UserFacingError
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_prize_edit import replace_prize_field
from tg_lottery.telegram.creator_resume_views import resumed_view
from tg_lottery.telegram.creator_suspension import CreatorSuspension, suspend_draft
from tg_lottery.telegram.draft_config import new_draft


def _draft():
    prizes = [{"name": "一等奖", "winner_count": 1}, {"name": "二等奖", "winner_count": 2}]
    return new_draft() | {
        "panel_message_id": 10,
        "creator_path": ["dashboard", "prizes"],
        "prizes": prizes,
        "prize_edit_index": 1,
        "prize_edit_original": dict(prizes[1]),
    }


@pytest.mark.parametrize("field,text", [("name", "新二等奖"), ("count", "5")])
async def test_field_reply_saves_only_selected_prize_and_routes_through_creator(field, text):
    data = _draft()
    original = deepcopy(data)
    prizes, sessions, message = _prize_fixture(data)
    creator = object.__new__(CampaignCreator)
    creator._prizes = prizes
    message.text = text
    assert creator.handles_action("draft_prize_edit:1")
    assert await creator.handle_text(message, f"draft:prize_edit_{field}", data)
    _, state, saved = sessions.set.await_args.args
    assert state == "draft:idle"
    assert saved["prizes"][0] == data["prizes"][0]
    assert saved["prizes"][1]["name" if field == "name" else "winner_count"] == (
        text if field == "name" else 5
    )
    assert saved["prize_edit_original"] == saved["prizes"][1]
    assert data == original


@pytest.mark.parametrize("value", ["0", "-1", "文字", str(2**31)])
async def test_invalid_count_does_not_save_or_change_original(value):
    data = _draft()
    flow, sessions, message = _prize_fixture(data)
    message.text = value
    with pytest.raises(UserFacingError):
        await flow.receive_edit_count(message, data)
    sessions.set.assert_not_awaited()
    assert data["prizes"][1] == {"name": "二等奖", "winner_count": 2}


def test_count_replacement_uses_final_total_and_retains_original_order():
    data = _draft()
    updated = replace_prize_field(data, field="winner_count", value=2**31 - 2)
    assert [prize["name"] for prize in updated["prizes"]] == ["一等奖", "二等奖"]
    assert sum(prize["winner_count"] for prize in updated["prizes"]) == 2**31 - 1
    with pytest.raises(UserFacingError, match="中奖总人数"):
        replace_prize_field(data, field="winner_count", value=2**31 - 1)


@pytest.mark.parametrize(
    "published", [{"published_campaign_id": "existing"}, {"publish_snapshot": {"id": "existing"}}]
)
def test_published_prize_cannot_be_edited(published):
    with pytest.raises(UserFacingError, match="不能修改奖品"):
        replace_prize_field(_draft() | published, field="winner_count", value=5)


@pytest.mark.parametrize(
    "changed",
    [
        [],
        [{"name": "另一项", "winner_count": 1}],
        [{"name": "一等奖", "winner_count": 1}, {"name": "已改变", "winner_count": 2}],
    ],
)
def test_changed_or_deleted_prize_cannot_be_overwritten(changed):
    data = _draft() | {"prizes": changed}
    with pytest.raises(UserFacingError, match="已经变化"):
        replace_prize_field(data, field="name", value="不应保存")


async def test_cancel_keeps_original_and_returns_to_selected_prize():
    data = _draft()
    flow, sessions, _message = _prize_fixture(data)
    query = SimpleNamespace(from_user=real_message().from_user, message=real_message())
    await flow.cancel_edit(query)
    assert sessions.set.await_args.args[1] == "draft:idle"
    assert sessions.set.await_args.args[2]["prizes"] == data["prizes"]
    assert "修改第 2 项奖品" in flow._renderer.render.await_args.kwargs["text"]


async def test_home_suspension_and_resume_preserve_edit_target_and_input_step():
    data = _draft()
    state = "draft:prize_edit_count"
    session = SimpleNamespace(state=state, data=data)
    sessions = SimpleNamespace(get=AsyncMock(return_value=session), set=AsyncMock())
    await suspend_draft(sessions, 201)
    suspended = sessions.set.await_args.args[2]
    assert suspended["suspended_state"] == state
    session.state, session.data = "draft:suspended", suspended
    renderer = SimpleNamespace(render=AsyncMock())
    query = SimpleNamespace(from_user=real_message().from_user, message=real_message())
    await CreatorSuspension(sessions=sessions, renderer=renderer).resume(
        query, SimpleNamespace(action="draft_resume_input")
    )
    assert sessions.set.await_args.args[1] == state
    assert "第 2 项" in renderer.render.await_args.kwargs["text"]
    assert sessions.set.await_args.args[2]["prizes"] == data["prizes"]
    _, restored_state, (text, _) = resumed_view(data, state=state, module=True)
    assert restored_state == "draft:idle" and "修改第 2 项奖品" in text


async def test_old_edit_callback_cannot_take_over_current_input(session_factory):
    factory = partial(UnitOfWork, session_factory)
    sessions = AdminSessionService(factory)
    tokens = CallbackTokenService(factory)
    data = _draft()
    await sessions.set(201, "draft:prize_edit_count", data)
    old = await sessions.issue_panel_context(201, 10)
    (token,) = await tokens.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(CallbackRequest("draft_prize_edit:0", payload=old.callback_payload()),),
    )
    await sessions.issue_panel_context(201, 10)
    with pytest.raises(RecoverableDraftCallback, match="old panel"):
        await tokens.consume(token, owner_user_id=201, chat_id=201)
    current = await sessions.get(201)
    assert current.state == "draft:prize_edit_count"
    assert current.data["prize_edit_index"] == 1
    assert current.data["prizes"] == data["prizes"]
