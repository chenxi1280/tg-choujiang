import asyncio
import base64
import os
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from aiogram.enums import UpdateType
from aiogram.types import Update
from sqlalchemy import func, select

from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.records import utcnow
from tg_lottery.application.request_context import bind_update_context, current_update_id
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
from tg_lottery.jobs.maintenance import (
    prune_processed_update_payloads,
    schedule_update_retention,
)
from tg_lottery.jobs.worker import JobWorker
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.inbox import InboxProcessor
from tg_lottery.telegram.outbox import OutboxWorker
from tg_lottery.telegram.polling import (
    ALLOWED_UPDATES,
    TelegramPollingAdapter,
    _routing_campaign_id,
    _routing_chat_id,
)


def _update(update_id: int, text: str = "/start") -> Update:
    timestamp = int(datetime.now(UTC).timestamp())
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": timestamp,
                "chat": {"id": 201, "type": "private"},
                "from": {"id": 201, "is_bot": False, "first_name": "Tester"},
                "text": text,
            },
        }
    )


def test_private_registration_update_routes_to_campaign() -> None:
    campaign_id = uuid4()
    update = _update(9, f"/start join_{campaign_id}")

    assert _routing_chat_id(update) == 201
    assert _routing_campaign_id(update) == str(campaign_id)


class FakePollingBot:
    def __init__(self) -> None:
        self.offsets: list[int | None] = []
        self.allowed_updates: list[str] = []

    async def get_updates(self, *, offset, timeout, allowed_updates):  # noqa: ASYNC109
        self.offsets.append(offset)
        self.allowed_updates = allowed_updates
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
        assert bot.allowed_updates == [item.value for item in UpdateType]
        assert ALLOWED_UPDATES == bot.allowed_updates
        assert offset.last_durable_update_id == 10
        assert update.state is UpdateState.RECEIVED


class GapPollingBot:
    async def get_updates(self, *, offset, timeout, allowed_updates):  # noqa: ASYNC109
        assert offset == 10
        return [_update(12)]


async def test_polling_marks_long_interruption_gap_for_integrity_review(
    session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            PollingOffset(
                bot_id=124,
                last_durable_update_id=9,
                last_poll_succeeded_at=utcnow() - timedelta(hours=25),
                updated_at=utcnow() - timedelta(hours=25),
            )
        )
    adapter = TelegramPollingAdapter(
        bot=GapPollingBot(),
        bot_id=124,
        owner_id="gap-owner",
        session_factory=session_factory,
        timeout_seconds=1,
        lease_seconds=60,
    )
    assert await adapter._acquire_or_renew_lease() is True
    assert await adapter.poll_once() == 1
    async with session_factory() as session:
        offset = await session.get(PollingOffset, 124)
        assert offset.last_durable_update_id == 12
        assert offset.integrity_status == "integrity_review_required"
        assert (offset.last_gap_from, offset.last_gap_to) == (10, 11)


class FlakyHandler:
    def __init__(self) -> None:
        self.calls = 0
        self.update_ids: list[int | None] = []

    async def handle(self, update: Update) -> None:
        self.calls += 1
        self.update_ids.append(current_update_id())
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
        assert handler.update_ids == [20, 20]


async def test_update_payload_retention_prunes_only_old_processed_rows(session_factory) -> None:
    now = utcnow()
    rows = (
        _stored_update(30, UpdateState.PROCESSED, now - timedelta(days=8)),
        _stored_update(31, UpdateState.FAILED, now - timedelta(days=8)),
        _stored_update(32, UpdateState.PROCESSED, now - timedelta(days=6)),
    )
    async with session_factory() as session, session.begin():
        session.add_all(rows)
    async with session_factory() as session, session.begin():
        assert await prune_processed_update_payloads(session, now) == 1
        await schedule_update_retention(session, now)
        await schedule_update_retention(session, now)
    async with session_factory() as session:
        stored = {
            row.update_id: row for row in (await session.scalars(select(TelegramUpdate))).all()
        }
        job_count = await session.scalar(select(func.count(ScheduledJob.id)))
        assert stored[30].payload is None
        assert stored[31].payload is not None
        assert stored[32].payload is not None
        assert job_count == 1


def _stored_update(update_id: int, state: UpdateState, processed_at: datetime) -> TelegramUpdate:
    update = _update(update_id)
    return TelegramUpdate(
        bot_id=123,
        update_id=update_id,
        update_type="message",
        event_at=processed_at,
        payload=update.model_dump(mode="json", exclude_none=True),
        payload_hash=str(update_id).zfill(64),
        state=state,
        attempts=1,
        processed_at=processed_at,
    )


class TimeoutGateway:
    async def execute(self, outbox, invite_link=None):
        raise TimeoutError("uncertain transport")


class SuccessGateway:
    async def execute(self, outbox, invite_link=None):
        return GatewayResult(remote_result={"ok": True}, remote_message_id=77)


def _cipher() -> SecretCipher:
    return SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())


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
    worker = OutboxWorker(
        owner_id="outbox-test",
        session_factory=session_factory,
        gateway=TimeoutGateway(),
        cipher=_cipher(),
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
    worker = OutboxWorker(
        owner_id="new-worker",
        session_factory=session_factory,
        gateway=SuccessGateway(),
        cipher=_cipher(),
    )
    assert await worker.process_once() is True
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
        assert row.state is OutboxState.CONFIRMED
        assert row.remote_message_id == 77


async def test_late_outbox_success_cannot_overwrite_unknown_recovery(session_factory) -> None:
    async with session_factory() as session, session.begin():
        row = TelegramOutbox(
            operation="send_winner_notice",
            aggregate_type="winner",
            aggregate_id="winner-late",
            recipient_chat_id=201,
            business_key="winner-notice-late",
            request_hash="d" * 64,
            payload={"campaign_name": "test", "prize_name": "prize"},
            state=OutboxState.CLAIMED,
            run_at=utcnow() - timedelta(minutes=5),
            lease_owner="old-worker",
            lease_until=utcnow() - timedelta(minutes=1),
            gateway_started_at=utcnow() - timedelta(minutes=2),
        )
        session.add(row)
        await session.flush()
        outbox_id = row.id
    old_worker = OutboxWorker(
        owner_id="old-worker",
        session_factory=session_factory,
        gateway=SuccessGateway(),
        cipher=_cipher(),
    )
    recovery_worker = OutboxWorker(
        owner_id="recovery-worker",
        session_factory=session_factory,
        gateway=SuccessGateway(),
        cipher=_cipher(),
    )
    await recovery_worker._recover_ambiguous()
    with pytest.raises(RuntimeError, match="ownership lost"):
        await old_worker._confirm(
            outbox_id, 1, GatewayResult(remote_result={"ok": True}, remote_message_id=88)
        )
    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        assert stored.state is OutboxState.UNKNOWN_HOLD
        assert stored.remote_message_id is None


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


async def test_expired_job_owner_cannot_finish_reclaimed_job(session_factory) -> None:
    async with session_factory() as session, session.begin():
        job = ScheduledJob(
            job_type="test",
            aggregate_id="lease-race",
            business_key="job:lease-race",
            payload={},
            run_at=utcnow() - timedelta(minutes=5),
            state=JobState.CLAIMED,
            lease_owner="old-owner",
            lease_until=utcnow() - timedelta(minutes=1),
            attempts=1,
        )
        session.add(job)
        await session.flush()
        job_id = job.id
    handlers = RecordingJobHandlers()
    old_worker = JobWorker(
        owner_id="old-owner",
        worker_name="old-worker",
        session_factory=session_factory,
        handlers=handlers,
        poll_seconds=0.1,
    )
    new_worker = JobWorker(
        owner_id="new-owner",
        worker_name="new-worker",
        session_factory=session_factory,
        handlers=handlers,
        poll_seconds=0.1,
    )
    claimed = await new_worker._claim_next()
    assert claimed is not None and claimed.id == job_id
    with pytest.raises(RuntimeError, match="ownership lost"):
        await old_worker._finish(job_id, JobState.SUCCEEDED, None)
    async with session_factory() as session:
        stored = await session.get(ScheduledJob, job_id)
        assert stored.state is JobState.CLAIMED
        assert stored.lease_owner == "new-owner"


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


async def test_callback_token_replay_is_allowed_only_for_same_durable_update(
    session_factory,
) -> None:
    service = CallbackTokenService(partial(UnitOfWork, session_factory))
    (token,) = await service.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(CallbackRequest("publish"),),
    )
    with bind_update_context(123, 88):
        first = await service.consume(token, owner_user_id=201, chat_id=201)
        replay = await service.consume(token, owner_user_id=201, chat_id=201)
    assert first == replay
    with bind_update_context(123, 89):
        with pytest.raises(AuthorizationDenied, match="already used"):
            await service.consume(token, owner_user_id=201, chat_id=201)
