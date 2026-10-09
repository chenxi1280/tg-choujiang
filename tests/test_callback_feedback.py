import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery

from tests.test_update_handler import _callback, _handler, _RecordingCallbackQuery
from tg_lottery.telegram import callback_feedback, command_input, member_callbacks


class SlowAcknowledgement(_RecordingCallbackQuery):
    def __init__(self):
        query = _callback("secure:gate-preset")
        super().__init__(query.data, query.message)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.finished = False

    async def answer(self, text=None, show_alert=False):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.finished = True
        await super().answer(text, show_alert)


async def test_slow_acknowledgement_does_not_hold_gate_operation():
    query = SlowAcknowledgement()
    handler = _handler()
    dispatched = asyncio.Event()

    async def dispatch(actual, data):
        assert actual is query and data == query.data
        assert query.started.is_set() and not query.finished
        dispatched.set()

    handler._dispatch_callback = dispatch
    task = asyncio.create_task(handler._callback(query))
    try:
        await asyncio.wait_for(dispatched.wait(), 1)
    finally:
        query.release.set()
        await asyncio.wait_for(task, 1)
    assert query.answers == [(None, False)]


METHOD = AnswerCallbackQuery(callback_query_id="test-feedback")


@pytest.mark.parametrize(
    "failure",
    [
        TelegramNetworkError(METHOD, "connection reset"),
        TelegramBadRequest(METHOD, "query is too old"),
        TelegramRetryAfter(METHOD, "flood control", retry_after=10),
        OSError("connection reset"),
    ],
)
async def test_ack_failure_keeps_business_and_records_actual_failure(failure, caplog):
    query = SlowAcknowledgement()
    query.answer = AsyncMock(side_effect=failure)
    handler = _handler()
    handler._dispatch_callback = AsyncMock()
    await handler._callback(query)
    handler._dispatch_callback.assert_awaited_once_with(query, query.data)
    query.answer.assert_awaited_once_with()
    record = next(r for r in caplog.records if r.msg == "callback acknowledgement failed: %s")
    assert record.error_code == type(failure).__name__
    assert record.completed is False
    assert record.elapsed_ms >= 0


async def test_ack_timeout_releases_update_and_cancels_request(monkeypatch, caplog):
    monkeypatch.setattr(callback_feedback, "ACK_TIMEOUT_SECONDS", 0.01)
    query = SlowAcknowledgement()
    handler = _handler()
    handler._dispatch_callback = AsyncMock()
    await asyncio.wait_for(handler._callback(query), 1)
    assert query.cancelled.is_set() and not query.finished
    handler._dispatch_callback.assert_awaited_once()
    assert any(
        getattr(record, "error_code", None) == "TimeoutError" and record.completed is False
        for record in caplog.records
    )


async def test_business_failure_keeps_original_error_and_reaps_acknowledgement():
    query = SlowAcknowledgement()
    handler = _handler()
    handler._dispatch_callback = AsyncMock(side_effect=RuntimeError("panel connection lost"))
    with pytest.raises(RuntimeError, match="panel connection lost"):
        await handler._callback(query)
    assert query.cancelled.is_set()
    assert query.answers == []


async def test_cancelled_update_reaps_both_requests():
    query = SlowAcknowledgement()
    handler = _handler()
    dispatched = asyncio.Event()
    business_cancelled = asyncio.Event()

    async def dispatch(*_):
        dispatched.set()
        try:
            await asyncio.Event().wait()
        finally:
            business_cancelled.set()

    handler._dispatch_callback = dispatch
    task = asyncio.create_task(handler._callback(query))
    await asyncio.wait_for(dispatched.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert query.cancelled.is_set() and business_cancelled.is_set()


@pytest.mark.parametrize("prefix", command_input.DEFERRED_CALLBACK_PREFIXES)
async def test_fact_dependent_feedback_remains_deferred(prefix):
    original = _callback(prefix + "test-scene")
    query = _RecordingCallbackQuery(original.data, original.message)
    handler = _handler()

    async def dispatch(actual, _):
        assert actual.answers == []
        await actual.answer("实际核验结果", show_alert=True)

    handler._dispatch_callback = dispatch
    await handler._callback(query)
    assert query.answers == [("实际核验结果", True)]


async def test_unexpected_ack_error_is_not_hidden():
    query = SlowAcknowledgement()
    query.answer = AsyncMock(side_effect=RuntimeError("invalid feedback adapter"))
    handler = _handler()
    handler._dispatch_callback = AsyncMock()
    with pytest.raises(RuntimeError, match="invalid feedback adapter"):
        await handler._callback(query)
    handler._dispatch_callback.assert_awaited_once()


async def test_concurrent_failures_preserve_business_error_and_log_ack_error(caplog):
    query = SlowAcknowledgement()
    query.answer = AsyncMock(side_effect=RuntimeError("invalid feedback adapter"))
    handler = _handler()
    handler._dispatch_callback = AsyncMock(side_effect=RuntimeError("panel connection lost"))
    with pytest.raises(RuntimeError, match="panel connection lost"):
        await handler._callback(query)
    assert "callback acknowledgement cleanup failed" in caplog.text
    assert "invalid feedback adapter" in caplog.text


async def test_result_alert_follows_pending_blank_acknowledgement():
    query = SlowAcknowledgement()
    handler = _handler()
    result_requested = asyncio.Event()
    original_answer = query.answer

    async def answer(text=None, show_alert=False):
        if text is None:
            await original_answer()
        else:
            query.answers.append((text, show_alert))

    async def dispatch(actual, _):
        result_requested.set()
        await member_callbacks.safe_answer(actual, "旧按钮已恢复", alert=True)

    query.answer = answer
    handler._dispatch_callback = dispatch
    task = asyncio.create_task(handler._callback(query))
    try:
        await asyncio.wait_for(result_requested.wait(), 1)
        assert query.answers == []
    finally:
        query.release.set()
        await asyncio.wait_for(task, 1)
    assert query.answers == [(None, False), ("旧按钮已恢复", True)]


async def test_feedback_context_does_not_hold_another_query_or_leak_after_completion():
    query = SlowAcknowledgement()
    original = _callback("member:rules:other-scene")
    other = _RecordingCallbackQuery(original.data, original.message)
    handler = _handler()

    async def dispatch(*_):
        await member_callbacks.safe_answer(other, "另一条查询", alert=True)
        assert other.answers == [("另一条查询", True)]
        query.release.set()

    handler._dispatch_callback = dispatch
    await asyncio.wait_for(handler._callback(query), 1)
    assert callback_feedback._ACTIVE_FEEDBACK.get() is None


async def test_result_rejection_is_visible_in_logs(caplog):
    query = SlowAcknowledgement()
    query.answer = AsyncMock(side_effect=TelegramBadRequest(METHOD, "query is too old"))
    await member_callbacks.safe_answer(query, "实际结果", alert=True)
    assert "callback result acknowledgement rejected by Telegram" in caplog.text


async def test_result_timeout_cancels_feedback_and_does_not_repeat_business(monkeypatch, caplog):
    monkeypatch.setattr(callback_feedback, "ACK_TIMEOUT_SECONDS", 0.01)
    query = SlowAcknowledgement()
    await asyncio.wait_for(member_callbacks.safe_answer(query, "已完成", alert=True), 1)
    assert query.cancelled.is_set() and not query.finished
    assert any(
        getattr(record, "operation", None) == "callback_result"
        and record.error_code == "TimeoutError"
        and record.completed is False
        for record in caplog.records
    )
