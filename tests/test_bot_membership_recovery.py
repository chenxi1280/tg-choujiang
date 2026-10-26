from functools import partial
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import GetChatAdministrators
from sqlalchemy import select

from tests.test_inbox_ordering import _processor, _stored_update
from tests.test_responsiveness import _rows
from tests.test_update_handler import _handler
from tg_lottery.application.groups import GroupService, TelegramAdmin
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import GroupAdmin
from tg_lottery.infrastructure.db.uow import UnitOfWork

DENIED = "Forbidden: bot was kicked from the supergroup chat"


def membership_update(update_id, *, admin):
    row = _stored_update(update_id)
    user = {"id": 123, "is_bot": True, "first_name": "Bot"}
    member = {"status": "left", "user": user}
    if admin:
        member = dict(
            user=user,
            status="administrator",
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=True,
            can_invite_users=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_send_welcome_messages=False,
        )
    row.update_type = "my_chat_member"
    row.payload = {
        "update_id": update_id,
        "my_chat_member": {
            "chat": {"id": -1001, "type": "supergroup", "title": "audit"},
            "from": {"id": 201, "is_bot": False, "first_name": "Admin"},
            "date": row.payload["message"]["date"],
            "old_chat_member": {"status": "member", "user": user},
            "new_chat_member": member,
        },
    }
    return row


async def test_lost_bot_access_revokes_cached_admins_and_releases_later_inputs(session_factory):
    groups = GroupService(partial(UnitOfWork, session_factory))
    await groups.sync_group(
        chat_id=-1001,
        title="test",
        timezone="Asia/Shanghai",
        permissions={},
        admins=(TelegramAdmin(201, "telegram_admin"),),
    )
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                membership_update(100, admin=True),
                membership_update(101, admin=False),
                _stored_update(102),
            ]
        )
    handler = _handler()
    handler._groups = groups
    handler._bot = AsyncMock()
    handler._message = AsyncMock()
    handler._bot.get_chat_administrators.side_effect = TelegramForbiddenError(
        GetChatAdministrators(chat_id=-1001), DENIED
    )
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    async with session_factory() as session:
        assert not await session.scalar(select(GroupAdmin.active))
    assert await processor.process_once() and await processor.process_once()
    rows = await _rows(session_factory)
    assert all(row.state is UpdateState.PROCESSED for row in rows)
    assert "cached administrators revoked" in rows[0].last_error
    assert DENIED in rows[0].last_error
    handler._bot.send_message.assert_not_awaited()
    handler._message.assert_awaited_once()


@pytest.mark.parametrize(
    "failure",
    [
        TelegramNetworkError(GetChatAdministrators(chat_id=-1001), "connection lost"),
        TelegramBadRequest(GetChatAdministrators(chat_id=-1001), "Bad Request: chat not found"),
        TelegramForbiddenError(GetChatAdministrators(chat_id=-1001), "Forbidden: unknown reason"),
        TelegramForbiddenError(GetChatAdministrators(chat_id=-999), DENIED),
    ],
)
async def test_uncertain_membership_errors_remain_retryable(session_factory, failure):
    async with session_factory() as session, session.begin():
        session.add_all([membership_update(100, admin=True), _stored_update(101)])
    handler = _handler()
    handler._groups = AsyncMock()
    handler._bot = AsyncMock()
    handler._bot.get_chat_administrators.side_effect = failure
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    assert not await processor.process_once()
    assert [r.state for r in await _rows(session_factory)] == [
        UpdateState.FAILED,
        UpdateState.RECEIVED,
    ]
    handler._groups.deactivate_group.assert_not_awaited()
    handler._bot.get_chat_administrators.assert_awaited_once_with(-1001)
    assert type(failure).__name__ in (await _rows(session_factory))[0].last_error


async def test_revoke_failure_cannot_be_marked_handled(session_factory):
    async with session_factory() as session, session.begin():
        session.add_all([membership_update(100, admin=True), _stored_update(101)])
    handler = _handler()
    handler._groups = AsyncMock()
    handler._groups.deactivate_group.side_effect = RuntimeError("database down")
    handler._bot = AsyncMock()
    handler._bot.get_chat_administrators.side_effect = TelegramForbiddenError(
        GetChatAdministrators(chat_id=-1001), DENIED
    )
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    assert not await processor.process_once()
    rows = await _rows(session_factory)
    assert rows[0].state is UpdateState.FAILED
    assert "database down" in rows[0].last_error


async def test_current_promotion_still_syncs_and_sends_reminder(session_factory):
    async with session_factory() as session, session.begin():
        session.add(membership_update(100, admin=True))
    handler = _handler()
    handler._groups = AsyncMock()
    handler._bot = AsyncMock()
    handler._bot.get_chat_administrators.return_value = []
    handler._bot.get_me.return_value.username = "test_bot"
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    assert (await _rows(session_factory))[0].state is UpdateState.PROCESSED
    handler._groups.sync_group.assert_awaited_once()
    handler._groups.deactivate_group.assert_not_awaited()
    handler._bot.send_message.assert_awaited_once()
