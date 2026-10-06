from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from tg_lottery.application.request_context import bind_update_context
from tg_lottery.telegram.creator_input_replay import recover_applied_input
from tg_lottery.telegram.draft_config import new_draft

BOT_ID = 999
SOURCE_ID = 100
USER_ID = 101


def _message():
    return Message(
        message_id=20,
        date=datetime.now(UTC),
        chat=Chat(id=USER_ID, type="private"),
        from_user=User(id=USER_ID, first_name="Owner", is_bot=False),
        text="原输入",
    )


@pytest.mark.parametrize("state", ["draft:idle", "draft:prize_count"])
async def test_applied_input_retry_redraws_saved_value_instead_of_parsing_it_as_next_field(state):
    data = new_draft() | {
        "panel_message_id": 10,
        "mutation_source_update_id": SOURCE_ID,
        "title": "已提交标题",
        "pending_prize_name": "待填写人数的无价值奖品",
    }
    session = SimpleNamespace(state=state, data=data)
    renderer = SimpleNamespace(edit=AsyncMock())
    with bind_update_context(BOT_ID, SOURCE_ID):
        assert await recover_applied_input(renderer, _message(), session)
    text = renderer.edit.await_args.kwargs["text"]
    assert "已提交标题" in text if state == "draft:idle" else "待填写人数的无价值奖品" in text
    assert data["pending_prize_name"] == "待填写人数的无价值奖品"
    assert data["prizes"] == []


async def test_older_input_cannot_be_applied_to_a_newer_field_or_reactivate_suspended_input(
    monkeypatch,
):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    session = SimpleNamespace(
        state="draft:prize_count", data={"mutation_source_update_id": SOURCE_ID + 1}
    )
    renderer = SimpleNamespace(edit=AsyncMock())
    with bind_update_context(BOT_ID, SOURCE_ID):
        assert await recover_applied_input(renderer, _message(), session)
    assert "没有按新字段重复提交" in answer.await_args.args[0]
    renderer.edit.assert_not_awaited()
    assert session.state == "draft:prize_count"


async def test_new_input_still_runs_its_normal_receiver():
    session = SimpleNamespace(
        state="draft:prize_count", data={"mutation_source_update_id": SOURCE_ID - 1}
    )
    renderer = SimpleNamespace(edit=AsyncMock())
    with bind_update_context(BOT_ID, SOURCE_ID):
        assert not await recover_applied_input(renderer, _message(), session)
    renderer.edit.assert_not_awaited()


@pytest.mark.parametrize("file_id", [None, "saved-synthetic-cover"])
async def test_cover_input_recovery_keeps_completion_and_truthful_clear_controls(file_id):
    data = new_draft() | {
        "panel_message_id": 10,
        "mutation_source_update_id": SOURCE_ID,
        "cover_file_id": file_id,
        "description": "说明保持独立",
        "creator_path": ["dashboard", "cover"],
    }
    renderer = SimpleNamespace(edit=AsyncMock())
    session = SimpleNamespace(state="draft:cover", data=data)
    with bind_update_context(BOT_ID, SOURCE_ID):
        assert await recover_applied_input(renderer, _message(), session)
    values = renderer.edit.await_args.kwargs
    actions = [action for row in values["rows"] for _, action in row]
    assert "draft_back" in actions
    assert ("draft_cover_clear" in actions) is bool(file_id)
    assert "当前已设置封面" in values["text"] if file_id else "请发送一张图片" in values["text"]
    assert session.data == data and session.state == "draft:cover"
