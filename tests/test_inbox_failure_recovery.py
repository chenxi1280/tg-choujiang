from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import GetChatMember, SendMessage
from sqlalchemy import select

from tests.test_inbox_ordering import _processor, _stored_update
from tests.test_responsiveness import _queue, _retry_ready, _rows
from tests.test_update_handler import _handler
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import TelegramUpdate
from tg_lottery.telegram import inbox
from tg_lottery.telegram.qualification import QualificationRateLimited


@pytest.mark.parametrize("wrapped", [False, True])
async def test_rate_limit_deadline_survives_restart_and_success_keeps_failure(
    session_factory, monkeypatch, wrapped
):
    await _queue(session_factory)
    failure = (
        QualificationRateLimited(60)
        if wrapped
        else TelegramRetryAfter(
            SendMessage(chat_id=201, text="reply"), "Too Many Requests", retry_after=60
        )
    )
    handler = AsyncMock()
    handler.handle.side_effect = failure
    processor = _processor(handler, session_factory)
    before = utcnow()
    assert await processor.process_once()
    rows = await _rows(session_factory)
    deadline = rows[0].next_attempt_at
    assert deadline >= before + timedelta(seconds=60)
    assert str(failure) in rows[0].last_failure
    await _retry_ready(session_factory)
    recovered_handler = AsyncMock()
    restarted = _processor(recovered_handler, session_factory)
    monkeypatch.setattr(inbox, "utcnow", lambda: deadline - timedelta(milliseconds=1))
    assert not await restarted.process_once()
    assert recovered_handler.handle.await_count == 0
    assert [r.attempts for r in await _rows(session_factory)] == [1, 0]
    monkeypatch.setattr(inbox, "utcnow", lambda: deadline + timedelta(milliseconds=1))
    assert await restarted.process_once() and await restarted.process_once()
    rows = await _rows(session_factory)
    assert all(r.state is UpdateState.PROCESSED for r in rows)
    assert rows[0].next_attempt_at is None and rows[0].last_error is None
    assert str(failure) in rows[0].last_failure


async def test_new_rate_limit_extends_deadline(session_factory, monkeypatch):
    await _queue(session_factory)
    handler = AsyncMock()
    handler.handle.side_effect = [
        TelegramRetryAfter(SendMessage(chat_id=201, text="reply"), "flood", retry_after=60),
        TelegramRetryAfter(SendMessage(chat_id=201, text="reply"), "flood", retry_after=120),
    ]
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    first_deadline = (await _rows(session_factory))[0].next_attempt_at
    now = first_deadline + timedelta(seconds=1)
    monkeypatch.setattr(inbox, "utcnow", lambda: now)
    assert await processor.process_once()
    assert (await _rows(session_factory))[0].next_attempt_at == now + timedelta(seconds=120)
    assert not await processor.process_once()


class RejectOnce:
    def __init__(self, reason):
        self.reason = reason
        self.methods = []

    async def __call__(self, method, request_timeout=None):
        self.methods.append(method)
        if len(self.methods) == 1:
            raise TelegramBadRequest(method, self.reason)
        return object()


@pytest.mark.parametrize(
    "reason", ["Bad Request: TOPIC_CLOSED", "Bad Request: message thread not found"]
)
async def test_topic_reply_rejection_releases_other_chat(session_factory, reason):
    first = _stored_update(100)
    first.payload["message"].update(
        chat={"id": -1001, "type": "supergroup", "is_forum": True},
        message_thread_id=17,
        is_topic_message=True,
        text="/本群编号",
    )
    async with session_factory() as session, session.begin():
        session.add_all([first, _stored_update(101)])
    bot = RejectOnce(reason)
    handler = _handler()

    # Use a real outgoing reply for the next private input too.
    async def private_reply(message):
        await message.answer("首页")

    handler._private_message = private_reply
    processor = _processor(handler, session_factory, bot=bot)
    assert await processor.process_once() and await processor.process_once()
    rows = await _rows(session_factory)
    assert all(row.state is UpdateState.PROCESSED for row in rows)
    assert reason in rows[0].last_error and rows[1].last_error is None
    assert bot.methods[0].message_thread_id == 17
    assert bot.methods[1].chat_id == 201


@pytest.mark.parametrize(
    "method", [GetChatMember(chat_id=201, user_id=201), SendMessage(chat_id=999, text="reply")]
)
async def test_topic_rejection_does_not_hide_queries_or_other_targets(session_factory, method):
    await _queue(session_factory)
    handler = AsyncMock()
    handler.handle.side_effect = TelegramBadRequest(method, "Bad Request: TOPIC_CLOSED")
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    assert not await processor.process_once()
    assert [r.state for r in await _rows(session_factory)] == [
        UpdateState.FAILED,
        UpdateState.RECEIVED,
    ]


async def test_recovered_non_rate_failure_retains_last_failure(session_factory):
    await _queue(session_factory)
    handler = AsyncMock()
    handler.handle.side_effect = [RuntimeError("database temporarily unavailable"), None]
    processor = _processor(handler, session_factory)
    assert await processor.process_once()
    await _retry_ready(session_factory)
    assert await processor.process_once()
    async with session_factory() as session:
        row = await session.scalar(select(TelegramUpdate).where(TelegramUpdate.update_id == 100))
        assert row.state is UpdateState.PROCESSED and row.last_error is None
        assert row.last_failure == "RuntimeError: database temporarily unavailable"
