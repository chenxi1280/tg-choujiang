import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import GetChatMember, SendMessage
from sqlalchemy import select

from tests.test_inbox_ordering import ReplyingHandler, _processor, _stored_update
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import UpdateState
from tg_lottery.healthcheck import _check_role
from tg_lottery.infrastructure.db import PollingOffset, TelegramUpdate
from tg_lottery.telegram import inbox


async def _queue(session_factory):
    async with session_factory() as session, session.begin():
        session.add_all([_stored_update(100), _stored_update(101)])


async def _rows(session_factory):
    async with session_factory() as session:
        return (
            await session.scalars(select(TelegramUpdate).order_by(TelegramUpdate.update_id))
        ).all()


async def _retry_ready(session_factory):
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(TelegramUpdate).where(TelegramUpdate.update_id == 100))
        row.updated_at = utcnow() - timedelta(seconds=6)


@pytest.mark.parametrize(
    "failure",
    [
        TelegramForbiddenError(
            SendMessage(chat_id=201, text="reply"), "Forbidden: bot was blocked by the user"
        ),
        TelegramBadRequest(SendMessage(chat_id=201, text="reply"), "Bad Request: chat not found"),
    ],
)
async def test_definite_source_reply_rejection_is_audited_and_releases_next_start(
    session_factory, failure, caplog
):
    await _queue(session_factory)
    handler = AsyncMock()
    handler.handle.side_effect = [failure, None]
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    assert await processor.process_once()
    rows = await _rows(session_factory)
    assert [r.state for r in rows] == [UpdateState.PROCESSED] * 2
    assert failure.message in rows[0].last_error
    assert "UI response rejected" in rows[0].last_error
    assert rows[1].last_error is None
    assert handler.handle.await_count == 2
    record = next(r for r in caplog.records if getattr(r, "operation", None) == "SendMessage")
    assert record.completed is False and record.chat_id == 201


@pytest.mark.parametrize(
    "failure",
    [
        TelegramForbiddenError(
            SendMessage(chat_id=999, text="reply"), "Forbidden: bot was blocked by the user"
        ),
        TelegramForbiddenError(
            GetChatMember(chat_id=201, user_id=201), "Forbidden: bot was blocked by the user"
        ),
        TelegramBadRequest(
            SendMessage(chat_id=201, text="reply"), "Bad Request: can't parse entities"
        ),
        TelegramBadRequest(
            SendMessage(chat_id=201, text="reply"), "Bad Request: not enough rights"
        ),
        TelegramNetworkError(SendMessage(chat_id=201, text="reply"), "connection lost"),
        RuntimeError("qualification failed"),
    ],
)
async def test_other_failures_remain_ordered_and_replayable(session_factory, failure):
    await _queue(session_factory)
    handler = AsyncMock()
    handler.handle.side_effect = failure
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    assert not await processor.process_once()
    rows = await _rows(session_factory)
    assert [r.state for r in rows] == [UpdateState.FAILED, UpdateState.RECEIVED]
    assert type(failure).__name__ in rows[0].last_error


class BlockingHandler:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def handle(self, update):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


async def test_handler_deadline_cancels_work_and_retains_ordered_retry(
    session_factory, monkeypatch
):
    await _queue(session_factory)
    monkeypatch.setattr(inbox, "_HANDLER_TIMEOUT_SECONDS", 0.02)
    handler = BlockingHandler()
    processor = _processor(handler, session_factory)
    assert await asyncio.wait_for(processor.process_once(), 1)
    assert handler.cancelled.is_set()
    assert not await processor.process_once()
    rows = await _rows(session_factory)
    assert [r.state for r in rows] == [UpdateState.FAILED, UpdateState.RECEIVED]
    assert "TimeoutError" in rows[0].last_error
    await _retry_ready(session_factory)
    replay = _processor(ReplyingHandler(), session_factory)
    assert await replay.process_once() and await replay.process_once()
    assert all(r.state is UpdateState.PROCESSED for r in await _rows(session_factory))


@pytest.mark.parametrize("shutdown", ["stop", "cancel"])
async def test_shutdown_releases_current_claim_without_waiting_five_minutes(
    session_factory, shutdown
):
    await _queue(session_factory)
    handler = BlockingHandler()
    processor = _processor(handler, session_factory)
    stop = asyncio.Event()
    task = asyncio.create_task(processor.run_forever(stop))
    await asyncio.wait_for(handler.started.wait(), 1)
    if shutdown == "stop":
        stop.set()
        await asyncio.wait_for(task, 1)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert handler.cancelled.is_set()
    rows = await _rows(session_factory)
    assert [r.state for r in rows] == [UpdateState.FAILED, UpdateState.RECEIVED]
    assert "processing interrupted" in rows[0].last_error
    await _retry_ready(session_factory)
    replay = _processor(ReplyingHandler(), session_factory)
    assert await replay.process_once() and await replay.process_once()
    assert [r.attempts for r in await _rows(session_factory)] == [2, 1]


async def test_cancel_during_success_projection_releases_owned_claim(session_factory):
    await _queue(session_factory)
    processor = _processor(ReplyingHandler(), session_factory)
    started = asyncio.Event()

    async def blocking_projection(*args):
        started.set()
        await asyncio.Event().wait()

    processor._mark_processed = blocking_projection
    task = asyncio.create_task(processor.process_once())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await _rows(session_factory))[0].state is UpdateState.FAILED


@pytest.mark.parametrize(
    "state", [UpdateState.RECEIVED, UpdateState.PROCESSING, UpdateState.FAILED]
)
async def test_fresh_polling_does_not_hide_stuck_inbox(session_factory, state):
    row = _stored_update(100)
    row.state = state
    row.created_at = utcnow() - timedelta(seconds=121)
    async with session_factory() as session, session.begin():
        session.add(row)
        session.add(PollingOffset(bot_id=123, last_poll_succeeded_at=utcnow(), updated_at=utcnow()))
    async with session_factory() as session:
        with pytest.raises(RuntimeError, match="queue is blocked: update=100"):
            await _check_role(session, "app", 123)


async def test_health_ignores_processed_reply_rejection_and_other_bot(session_factory):
    rejected = _stored_update(100)
    rejected.state = UpdateState.PROCESSED
    rejected.last_error = "UI response rejected: bot blocked"
    rejected.created_at = utcnow() - timedelta(seconds=121)
    other = _stored_update(101)
    other.bot_id = 999
    other.created_at = rejected.created_at
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                rejected,
                other,
                PollingOffset(bot_id=123, last_poll_succeeded_at=utcnow(), updated_at=utcnow()),
            ]
        )
    async with session_factory() as session:
        await _check_role(session, "app", 123)


@pytest.mark.parametrize(
    "reason",
    [
        "Bad Request: not enough rights to send text messages to the chat",
        "Bad Request: channel direct messages topic must be specified",
    ],
)
async def test_permission_reminder_rejection_preserves_membership_update(session_factory, reason):
    from aiogram.methods import SendMessage

    from tests.test_update_handler import _handler

    row = _stored_update(100)
    timestamp = row.payload["message"]["date"]
    user = {"id": 123, "is_bot": True, "first_name": "Bot"}
    row.update_type = "my_chat_member"
    row.payload = {
        "update_id": 100,
        "my_chat_member": {
            "chat": {"id": -1001, "type": "supergroup", "title": "test"},
            "from": {"id": 201, "is_bot": False, "first_name": "Admin"},
            "date": timestamp,
            "old_chat_member": {"status": "left", "user": user},
            "new_chat_member": {"status": "member", "user": user},
        },
    }
    async with session_factory() as session, session.begin():
        session.add_all([row, _stored_update(101)])
    handler = _handler()
    handler._groups = AsyncMock()
    handler._bot = AsyncMock()

    async def reject(*args):
        handler._groups.deactivate_group.assert_awaited_once()
        raise TelegramBadRequest(
            SendMessage(chat_id=-1001, text="permissions"),
            reason,
        )

    handler._bot.send_message.side_effect = reject
    processor = _processor(handler, session_factory)
    assert await processor.process_once() and await processor.process_once()
    rows = await _rows(session_factory)
    assert rows[0].state is UpdateState.PROCESSED
    assert "UI response rejected" in rows[0].last_error
    handler._groups.deactivate_group.assert_awaited_once()


async def test_channel_direct_membership_does_not_block_next_command(session_factory):
    from tests.test_update_handler import _handler

    row = _stored_update(100)
    timestamp = row.payload["message"]["date"]
    user = {"id": 123, "is_bot": True, "first_name": "Bot"}
    row.update_type = "my_chat_member"
    row.payload = {
        "update_id": 100,
        "my_chat_member": {
            "chat": {"id": -1001, "type": "supergroup", "is_direct_messages": True},
            "from": {"id": 201, "is_bot": False, "first_name": "Admin"},
            "date": timestamp,
            "old_chat_member": {"status": "left", "user": user},
            "new_chat_member": {"status": "member", "user": user},
        },
    }
    async with session_factory() as session, session.begin():
        session.add_all([row, _stored_update(101)])
    handler = _handler()
    handler._groups = AsyncMock()
    handler._bot = AsyncMock()
    handler._message = AsyncMock()
    processor = _processor(handler, session_factory)
    assert await processor.process_once() and await processor.process_once()
    assert all(r.state is UpdateState.PROCESSED for r in await _rows(session_factory))
    handler._groups.deactivate_group.assert_not_awaited()
    handler._bot.send_message.assert_not_awaited()
    handler._bot.get_chat_administrators.assert_not_awaited()
    handler._message.assert_awaited_once()
