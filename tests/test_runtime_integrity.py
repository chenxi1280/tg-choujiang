import asyncio
import base64
import os
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from aiogram.types import Update
from sqlalchemy import func, select

from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import JobState, OutboxState, UpdateState
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.infrastructure.db import (
    DeliveryAttempt,
    PollingOffset,
    ScheduledJob,
    TelegramOutbox,
    TelegramUpdate,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.jobs.worker import JobWorker
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.inbox import InboxProcessor
from tg_lottery.telegram.outbox import OutboxWorker
from tg_lottery.telegram.polling import TelegramPollingAdapter


def _update(update_id: int) -> Update:
    timestamp = int(datetime.now(UTC).timestamp())
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": timestamp,
                "chat": {"id": 201, "type": "private"},
                "from": {"id": 201, "is_bot": False, "first_name": "Tester"},
                "text": "/start",
            },
        }
    )


class FakePollingBot:
    def __init__(self) -> None:
        self.offsets: list[int | None] = []

    async def get_updates(self, *, offset, timeout, allowed_updates):  # noqa: ASYNC109
        self.offsets.append(offset)
        return [_update(10)] if len(self.offsets) == 1 else []


async def test_polling_persists_batch_before_advancing_offset(session_factory) -> None:
    bot = FakePollingBot()
    adapter = TelegramPollingAdapter(
        bot=bot,
        bot_id=123,
        owner_id="test-owner",
        session_factory=session_factory,
        timeout_seconds=1,
        lease_seconds=60,
    )
    assert await adapter._acquire_or_renew_lease() is True
    assert await adapter.poll_once() == 1
    assert await adapter.poll_once() == 0
    async with session_factory() as session:
        offset = await session.get(PollingOffset, 123)
        update = await session.scalar(select(TelegramUpdate))
        assert bot.offsets == [None, 11]
        assert offset.last_durable_update_id == 10
        assert update.state is UpdateState.RECEIVED


class FlakyHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, update: Update) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("planned failure")


async def test_inbox_failure_remains_replayable(session_factory) -> None:
    update = _update(20)
    async with session_factory() as session, session.begin():
        session.add(
            TelegramUpdate(
                bot_id=123,
                update_id=20,
                update_type="message",
                event_at=datetime.now(UTC),
                payload=update.model_dump(mode="json", exclude_none=True),
                payload_hash="a" * 64,
                state=UpdateState.RECEIVED,
                attempts=0,
            )
        )
    handler = FlakyHandler()
    processor = InboxProcessor(
        bot_id=123,
        handler=handler,
        session_factory=session_factory,
    )
    assert await processor.process_once() is True
    async with session_factory() as session:
        row = await session.scalar(select(TelegramUpdate))
        assert row.state is UpdateState.FAILED
    assert await processor.process_once() is True
    async with session_factory() as session:
        row = await session.scalar(select(TelegramUpdate))
        assert row.state is UpdateState.PROCESSED
        assert row.attempts == 2


class TimeoutGateway:
    async def execute(self, outbox, invite_link=None):
        raise TimeoutError("uncertain transport")


class SuccessGateway:
    async def execute(self, outbox, invite_link=None):
        return GatewayResult(remote_result={"ok": True}, remote_message_id=77)


async def test_unknown_delivery_is_held_and_not_retried(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            TelegramOutbox(
                operation="send_winner_notice",
                aggregate_type="winner",
                aggregate_id="winner-1",
                recipient_chat_id=201,
                business_key="winner-notice-1",
                request_hash="b" * 64,
                payload={"campaign_name": "test", "prize_name": "prize"},
                state=OutboxState.PENDING,
                run_at=utcnow(),
            )
        )
    cipher = SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
    worker = OutboxWorker(
        owner_id="outbox-test",
        session_factory=session_factory,
        gateway=TimeoutGateway(),
        cipher=cipher,
    )
    assert await worker.process_once() is True
    assert await worker.process_once() is False
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
        attempts = await session.scalar(select(func.count(DeliveryAttempt.id)))
        assert row.state is OutboxState.UNKNOWN_HOLD
        assert row.gateway_started_at is not None
        assert attempts == 1


async def test_expired_claim_before_gateway_is_safely_recovered(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            TelegramOutbox(
                operation="send_winner_notice",
                aggregate_type="winner",
                aggregate_id="winner-2",
                recipient_chat_id=201,
                business_key="winner-notice-2",
                request_hash="c" * 64,
                payload={"campaign_name": "test", "prize_name": "prize"},
                state=OutboxState.CLAIMED,
                run_at=utcnow() - timedelta(minutes=5),
                lease_owner="dead-worker",
                lease_until=utcnow() - timedelta(minutes=1),
            )
        )
    cipher = SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
    worker = OutboxWorker(
        owner_id="new-worker",
        session_factory=session_factory,
        gateway=SuccessGateway(),
        cipher=cipher,
    )
    assert await worker.process_once() is True
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
        assert row.state is OutboxState.CONFIRMED
        assert row.remote_message_id == 77


class RecordingJobHandlers:
    def __init__(self) -> None:
        self.job_ids: list[object] = []

    async def handle(self, job: ScheduledJob) -> None:
        await asyncio.sleep(0.05)
        self.job_ids.append(job.id)


async def test_two_workers_claim_distinct_jobs(session_factory) -> None:
    async with session_factory() as session, session.begin():
        for index in range(2):
            session.add(
                ScheduledJob(
                    job_type="test",
                    aggregate_id=str(index),
                    business_key=f"job:{index}",
                    payload={},
                    run_at=utcnow(),
                    state=JobState.PENDING,
                    attempts=0,
                )
            )
    handlers = RecordingJobHandlers()
    workers = [
        JobWorker(
            owner_id=f"owner-{index}",
            worker_name=f"worker-{index}",
            session_factory=session_factory,
            handlers=handlers,
            poll_seconds=0.1,
        )
        for index in range(2)
    ]
    results = await asyncio.gather(*(worker.process_once() for worker in workers))
    async with session_factory() as session:
        states = (await session.scalars(select(ScheduledJob.state))).all()
        assert results == [True, True]
        assert len(set(handlers.job_ids)) == 2
        assert states == [JobState.SUCCEEDED, JobState.SUCCEEDED]


async def test_callback_token_is_owner_bound_and_single_use(session_factory) -> None:
    service = CallbackTokenService(partial(UnitOfWork, session_factory))
    (token,) = await service.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(CallbackRequest("publish"),),
    )
    with pytest.raises(AuthorizationDenied, match="owner"):
        await service.consume(token, owner_user_id=202, chat_id=201)
    grant = await service.consume(token, owner_user_id=201, chat_id=201)
    assert grant.action == "publish"
    with pytest.raises(AuthorizationDenied, match="expired or already used"):
        await service.consume(token, owner_user_id=201, chat_id=201)
