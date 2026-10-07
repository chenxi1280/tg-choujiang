from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message
from sqlalchemy import func, select

import tg_lottery.jobs.worker as job_worker_module
import tg_lottery.telegram.outbox as outbox_module
import tg_lottery.telegram.outbox_records as outbox_records_module
from tests.helpers import seed_campaign
from tests.test_outbox_projection import _cipher
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.enums import DeliveryCertainty, JobState, OutboxState
from tg_lottery.infrastructure.db import DeliveryAttempt, ScheduledJob, TelegramOutbox
from tg_lottery.jobs.handlers import JobHandlers
from tg_lottery.jobs.worker import JobWorker
from tg_lottery.telegram.gateway import GatewayResult, TelegramGateway
from tg_lottery.telegram.outbox import OutboxWorker
from tg_lottery.telegram.outbox_projection import project_outbox_success

DELETE_DELAY_SECONDS = 15
NOTICE_MESSAGE_ID = 811
GROUP_CHAT_ID = -100123
JOB_POLL_SECONDS = 0.1
BEFORE_DUE = timedelta(microseconds=1)


class NoticeBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent = []
        self.deleted = []

    async def send_message(self, chat_id, text, **kwargs) -> Message:
        self.sent.append((chat_id, text, kwargs))
        if self.error is not None:
            raise self.error
        return Message(
            message_id=NOTICE_MESSAGE_ID,
            date=utcnow(),
            chat=Chat(id=chat_id, type="supergroup"),
            text=text,
        )

    async def delete_message(self, chat_id, message_id) -> bool:
        self.deleted.append((chat_id, message_id))
        return True


def _delivery_worker(session_factory, bot: NoticeBot) -> OutboxWorker:
    cipher = _cipher()
    return OutboxWorker(
        owner_id="notice-delivery-worker",
        session_factory=session_factory,
        gateway=TelegramGateway(bot, cipher),
        cipher=cipher,
    )


def _deletion_worker(session_factory) -> JobWorker:
    handlers = JobHandlers(
        bot=object(),
        session_factory=session_factory,
        participants=object(),
        invitations=object(),
        settlement=object(),
        drawing=object(),
    )
    return JobWorker(
        owner_id="notice-deletion-worker",
        worker_name="notice-deletion-worker",
        session_factory=session_factory,
        handlers=handlers,
        poll_seconds=JOB_POLL_SECONDS,
    )


async def _seed_notice(session_factory):
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session)
        row = add_outbox(
            session,
            operation="send_progress_notice",
            aggregate_type="participant",
            aggregate_id=str(participant.id),
            recipient_chat_id=group.chat_id,
            business_key=f"delayed-notice:{participant.id}",
            payload={
                "user_id": participant.user_id,
                "display_name": participant.display_name,
                "campaign_name": campaign.name,
                "campaign_ref": campaign.public_ref,
                "text": "测试通知",
                "ticket_status": "当前有效抽奖次数 1。",
                "delete_after_seconds": DELETE_DELAY_SECONDS,
            },
        )
        await session.flush()
        return row.id


async def _deletion_facts(session_factory, outbox_id):
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, outbox_id)
        attempt = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == outbox_id)
        )
        jobs = tuple(await session.scalars(select(ScheduledJob)))
        assert row.state is OutboxState.CONFIRMED
        assert row.projected_at is not None
        assert attempt.certainty is DeliveryCertainty.CONFIRMED
        assert len(jobs) == 1
        job = jobs[0]
        assert job.job_type == "delete_telegram_message"
        assert job.run_at == attempt.finished_at + timedelta(seconds=DELETE_DELAY_SECONDS)
        assert job.payload == {"chat_id": GROUP_CHAT_ID, "message_id": NOTICE_MESSAGE_ID}
        assert job.business_key == f"delete_message:{GROUP_CHAT_ID}:{NOTICE_MESSAGE_ID}"
        return job.id, job.run_at


async def test_confirmed_notice_deletes_only_after_confirmed_receipt_plus_delay(
    session_factory, monkeypatch
) -> None:
    outbox_id = await _seed_notice(session_factory)
    confirmed_at = utcnow()
    monkeypatch.setattr(outbox_records_module, "utcnow", lambda: confirmed_at)
    bot = NoticeBot()
    delivery = _delivery_worker(session_factory, bot)
    assert await delivery.process_once()
    job_id, due_at = await _deletion_facts(session_factory, outbox_id)
    deletion = _deletion_worker(session_factory)
    monkeypatch.setattr(job_worker_module, "utcnow", lambda: due_at - BEFORE_DUE)
    assert not await deletion.process_once()
    assert bot.deleted == []
    monkeypatch.setattr(job_worker_module, "utcnow", lambda: due_at)
    assert await deletion.process_once()
    async with session_factory() as session:
        job = await session.get(ScheduledJob, job_id)
        delete = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "delete_message")
        )
        assert job.state is JobState.SUCCEEDED
        assert delete.state is OutboxState.PENDING
        assert delete.recipient_chat_id == GROUP_CHAT_ID
        assert delete.payload == {"message_id": NOTICE_MESSAGE_ID}
    assert await delivery.process_once()
    assert len(bot.sent) == 1
    assert bot.deleted == [(GROUP_CHAT_ID, NOTICE_MESSAGE_ID)]
    async with session_factory() as session:
        delete = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "delete_message")
        )
        assert delete.state is OutboxState.CONFIRMED


async def test_repeated_confirmed_projection_keeps_one_deletion_job(session_factory) -> None:
    outbox_id = await _seed_notice(session_factory)
    bot = NoticeBot()
    assert await _delivery_worker(session_factory, bot).process_once()
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, outbox_id, with_for_update=True)
        result = GatewayResult({}, remote_message_id=row.remote_message_id)
        await project_outbox_success(session, row, result, cipher=_cipher())
        await project_outbox_success(session, row, result, cipher=_cipher())
    await _deletion_facts(session_factory, outbox_id)
    assert len(bot.sent) == 1


async def test_deletion_projection_retry_preserves_confirmation_without_resending(
    session_factory, monkeypatch
) -> None:
    outbox_id = await _seed_notice(session_factory)
    confirmed_at = utcnow()
    monkeypatch.setattr(outbox_records_module, "utcnow", lambda: confirmed_at)
    original_projection = outbox_module.project_outbox_success

    async def fail_projection(*_args, **_kwargs):
        raise RuntimeError("deletion storage unavailable")

    monkeypatch.setattr(outbox_module, "project_outbox_success", fail_projection)
    bot = NoticeBot()
    worker = _delivery_worker(session_factory, bot)
    assert await worker.process_once()
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, outbox_id, with_for_update=True)
        assert row.state is OutboxState.CONFIRMED
        assert row.remote_message_id == NOTICE_MESSAGE_ID
        assert row.projected_at is None
        assert row.last_error == "projection: RuntimeError: deletion storage unavailable"
        assert await session.scalar(select(func.count(ScheduledJob.id))) == 0
        row.run_at = utcnow()
    monkeypatch.setattr(outbox_module, "project_outbox_success", original_projection)
    assert await worker.process_once()
    await _deletion_facts(session_factory, outbox_id)
    async with session_factory() as session:
        attempts = tuple(await session.scalars(select(DeliveryAttempt)))
        row = await session.get(TelegramOutbox, outbox_id)
        assert len(attempts) == 1
        assert attempts[0].finished_at == confirmed_at
        assert attempts[0].remote_result["message_id"] == NOTICE_MESSAGE_ID
        assert row.last_error is None
    assert len(bot.sent) == 1
    assert bot.deleted == []


@pytest.mark.parametrize(
    "case",
    [
        ("rejected", OutboxState.PRE_ACCEPT_FAILED, DeliveryCertainty.EXPLICIT_REJECTION),
        ("unknown", OutboxState.UNKNOWN_HOLD, DeliveryCertainty.UNKNOWN),
    ],
)
async def test_unconfirmed_notice_never_schedules_deletion(session_factory, case) -> None:
    kind, state, certainty = case
    outbox_id = await _seed_notice(session_factory)
    method = SendMessage(chat_id=GROUP_CHAT_ID, text="测试通知")
    error_type = TelegramBadRequest if kind == "rejected" else TelegramNetworkError
    bot = NoticeBot(error_type(method=method, message="delivery failed"))
    worker = _delivery_worker(session_factory, bot)
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, outbox_id)
        attempt = await session.scalar(select(DeliveryAttempt))
        assert row.state is state
        assert row.remote_message_id is None
        assert row.projected_at is None
        assert attempt.certainty is certainty
        assert await session.scalar(select(func.count(ScheduledJob.id))) == 0
    assert len(bot.sent) == 1
    assert bot.deleted == []
