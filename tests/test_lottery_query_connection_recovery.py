from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from sqlalchemy import func, select

from tests.test_invite_connection_failures import network_error
from tests.test_notice_connection_recovery import _seed_notice
from tests.test_notice_deletion import _deletion_worker, _delivery_worker
from tests.test_r14_queries import message
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import DeliveryAttempt, Participant, ScheduledJob, TelegramOutbox
from tg_lottery.telegram.lottery_queries import LotteryQueries


def _missing_reply(chat_id=901):
    return TelegramBadRequest(
        SendMessage(chat_id=chat_id, text="progress"),
        "Bad Request: message to be replied not found",
    )


async def _queue_query(sessions, chat_id=901):
    flow = LotteryQueries(QueryService(sessions), bot_username="testbot")
    command = message("/抽奖进度", chat_id=chat_id).model_copy(update={"message_thread_id": None})
    await flow.message(command, command.text)
    async with sessions() as session:
        row = await session.scalar(select(TelegramOutbox))
        return row.id, row.business_key, row.request_hash


@pytest.mark.parametrize("missing_reply", [False, True])
@pytest.mark.parametrize("chat_id", [-100123, 901])
async def test_query_safe_retry_confirms_once_and_preserves_chat_retention(
    session_factory, missing_reply, chat_id
):
    row_id, business_key, request_hash = await _queue_query(session_factory, chat_id)
    reply = message("progress", chat_id=chat_id, number=811)
    results = [network_error("connect"), reply]
    if missing_reply:
        results = [_missing_reply(chat_id), results[0], _missing_reply(chat_id), reply]
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=results), delete_message=AsyncMock(return_value=True)
    )
    worker = _delivery_worker(session_factory, bot)
    assert await worker.process_once()
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        assert row.state is OutboxState.RETRY_SCHEDULED
        assert row.remote_message_id is None
        assert await session.scalar(select(func.count()).select_from(ScheduledJob)) == 0
        row.run_at = utcnow()
    assert await worker.process_once()
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        assert row.state is OutboxState.CONFIRMED
        assert (row.business_key, row.request_hash) == (business_key, request_hash)
        assert row.remote_message_id == 811
        attempts = tuple(
            await session.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.outbox_id == row_id)
                .order_by(DeliveryAttempt.attempt_number)
            )
        )
        assert [a.certainty.value for a in attempts] == ["explicit_rejection", "confirmed"]
        job = await session.scalar(select(ScheduledJob))
        if chat_id < 0:
            assert job.run_at == attempts[-1].finished_at + timedelta(seconds=20)
            assert job.payload["message_id"] == 811
            job.run_at = utcnow()
        else:
            assert job is None
        assert await session.scalar(select(func.count()).select_from(Participant)) == 0
    assert await _deletion_worker(session_factory).process_once() == (chat_id < 0)
    if chat_id < 0:
        assert await worker.process_once()
        bot.delete_message.assert_awaited_once_with(chat_id, 811)
    else:
        bot.delete_message.assert_not_awaited()
    assert bot.send_message.await_count == (4 if missing_reply else 2)
    assert not await worker.process_once()


@pytest.mark.parametrize("missing_reply", [False, True])
@pytest.mark.parametrize("kind", ["timeout", "response_reset", "text_only", "unverified_connect"])
async def test_private_query_ambiguous_send_is_not_replayed_or_scheduled_for_deletion(
    session_factory, missing_reply, kind
):
    row_id, _, _ = await _queue_query(session_factory)
    errors = [network_error(kind)]
    if missing_reply:
        errors.insert(0, _missing_reply())
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=errors))
    worker = _delivery_worker(session_factory, bot)
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        assert row.state is OutboxState.UNKNOWN_HOLD
        assert row.remote_message_id is None
        assert await session.scalar(select(func.count()).select_from(ScheduledJob)) == 0
    assert bot.send_message.await_count == (2 if missing_reply else 1)


async def test_existing_unknown_query_is_not_replayed_after_retry_fix(session_factory):
    row_id = await _seed_notice(session_factory, "send_lottery_query", unknown=True)
    bot = SimpleNamespace(send_message=AsyncMock())
    assert not await _delivery_worker(session_factory, bot).process_once()
    bot.send_message.assert_not_awaited()
    async with session_factory() as session:
        assert (await session.get(TelegramOutbox, row_id)).state is OutboxState.UNKNOWN_HOLD
        assert await session.scalar(select(func.count()).select_from(DeliveryAttempt)) == 1
