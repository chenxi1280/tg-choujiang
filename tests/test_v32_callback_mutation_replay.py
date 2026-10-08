from functools import partial

import pytest

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.errors import AppliedDraftCallback
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.creator_group_bonus import apply_action
from tg_lottery.telegram.creator_resume_views import resumed_view
from tg_lottery.telegram.draft_config import new_draft

OWNER = 201
BOT = 123
UPDATE = 901
CHAT = -1001


async def _setup(session_factory, action):
    factory = partial(UnitOfWork, session_factory)
    sessions, tokens = AdminSessionService(factory), CallbackTokenService(factory)
    data = new_draft() | {"participation_groups": [{"id": CHAT, "title": "验收群"}]}
    data = apply_action(data, f"draft_bonus_activity_group:{CHAT}")
    await sessions.set(OWNER, "draft:idle", data)
    context = await sessions.issue_panel_context(OWNER, 10)
    (token,) = await tokens.create_many(
        owner_user_id=OWNER,
        chat_id=OWNER,
        requests=(CallbackRequest(action, payload=context.callback_payload()),),
    )
    return sessions, tokens, token


async def test_same_update_recovery_does_not_apply_a_committed_numeric_mutation_twice(
    session_factory,
):
    sessions, tokens, token = await _setup(session_factory, "draft_bonus_activity_cycle_inc")
    with bind_update_context(BOT, UPDATE):
        first = await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER)
        current = await sessions.get(OWNER)
        changed = apply_action(current.data, first.action)
        await sessions.set(OWNER, current.state, changed)
        # Telegram edit failure happens after this committed write; Inbox retries the same update.
        with pytest.raises(AppliedDraftCallback, match="already committed"):
            await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER)
    final = await sessions.get(OWNER)
    assert final.data["bonus_working_copy"]["rule"]["messages_per_cycle"] == 6


async def test_consumed_callback_without_committed_mutation_still_retries(session_factory):
    _, tokens, token = await _setup(session_factory, "draft_bonus_activity_cycle_inc")
    with bind_update_context(BOT, UPDATE):
        first = await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER)
        assert await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER) == first


async def test_publishing_stage_write_does_not_block_same_source_publication_recovery(
    session_factory,
):
    sessions, tokens, token = await _setup(session_factory, "draft_publish_confirm")
    with bind_update_context(BOT, UPDATE):
        first = await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER)
        current = await sessions.get(OWNER)
        await sessions.set(OWNER, "draft:publishing", current.data)
        assert await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER) == first


def test_recovery_of_committed_bonus_parameter_keeps_the_group_editor():
    data = new_draft() | {"participation_groups": [{"id": CHAT, "title": "验收群"}]}
    data = apply_action(data, f"draft_bonus_activity_group:{CHAT}")
    data = apply_action(data, "draft_bonus_activity_cycle_inc")
    changed, state, (text, rows) = resumed_view(data, state="draft:idle", module=False)
    assert state == "draft:idle" and changed["bonus_working_copy"] == data["bonus_working_copy"]
    assert "6" in text
    assert any(action == "draft_bonus_input_cycle" for row in rows for _, action in row)


async def test_applied_callback_routes_to_current_group_recovery_instead_of_expiry_navigation():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.test_update_handler import _callback, _handler

    handler = _handler()
    handler._tokens = SimpleNamespace(
        consume=AsyncMock(side_effect=AppliedDraftCallback("applied"))
    )
    admin = SimpleNamespace(
        recover_applied_draft_callback=AsyncMock(), recover_draft_callback=AsyncMock()
    )
    handler._admin_flow = admin
    query = _callback("secure:recorded-source")
    await handler._dispatch_secure_callback(query, query.data)
    admin.recover_applied_draft_callback.assert_awaited_once_with(query)
    admin.recover_draft_callback.assert_not_awaited()


async def test_committed_home_navigation_recovery_preserves_home_destination(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.types import Message

    from tests.test_update_handler import _callback
    from tg_lottery.telegram.campaign_creator import CampaignCreator

    query = _callback("secure:recorded-home")
    creator = object.__new__(CampaignCreator)
    creator._sessions = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(state="draft:suspended", data=new_draft()))
    )
    creator._renderer = SimpleNamespace(edit=AsyncMock())
    edit = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    await creator.recover_applied_callback(query)
    assert edit.await_args.args[0].startswith("✨ 抽奖助手")
    assert len(edit.await_args.kwargs["reply_markup"].inline_keyboard) == 3
    creator._renderer.edit.assert_not_awaited()


async def test_navigation_checkpoint_does_not_skip_unfinished_remote_list_loading(session_factory):
    sessions, tokens, token = await _setup(session_factory, "draft_group")
    with bind_update_context(BOT, UPDATE):
        first = await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER)
        current = await sessions.get(OWNER)
        await sessions.set(OWNER, current.state, current.data)
        # Page metadata precedes Telegram group inspection; its failure must still be retried.
        assert await tokens.consume(token, owner_user_id=OWNER, chat_id=OWNER) == first
