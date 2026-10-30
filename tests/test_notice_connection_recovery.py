from dataclasses import replace
from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import DeleteMessage, SendMessage
from sqlalchemy import select

from tests.helpers import seed_campaign
from tests.test_invite_connection_failures import network_error
from tests.test_outbox_projection import _cipher
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.enums import DeliveryCertainty, JobState, OutboxState
from tg_lottery.infrastructure.db import AuditLog, DeliveryAttempt, ScheduledJob, TelegramOutbox
from tg_lottery.ops.delivery_health import read_delivery_health, render_health
from tg_lottery.ops.reconcile_invite_connection import RepairOptions, reconcile_notice_connection
from tg_lottery.telegram.delivery_failures import failed_before_single_request
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.outbox import OutboxWorker

NOTICE_OPERATIONS = (
    "send_progress_notice",
    "send_temporary_reminder",
    "send_lottery_query",
    "delete_message",
)
RUNTIME_REF = "verified-artifact:aiogram=3.31.0:aiohttp=3.14.5"


class Gateway:
    def __init__(self, errors: list[str | Exception]) -> None:
        self.errors = errors
        self.calls = []
        self.requests = []

    async def execute(self, request, _link):
        self.calls.append(request.operation)
        self.requests.append(request)
        if self.errors:
            error = self.errors.pop(0)
            raise error if isinstance(error, Exception) else network_error(error)
        return GatewayResult({"ok": True}, remote_message_id=808)


def _worker(sessions, gateway):
    return OutboxWorker(
        owner_id="notice-connection-test",
        session_factory=sessions,
        gateway=gateway,
        cipher=_cipher(),
    )


@pytest.mark.parametrize("operation", NOTICE_OPERATIONS)
def test_only_real_connector_cause_is_safe_for_single_notice(operation):
    assert failed_before_single_request(operation, network_error("connect"))
    for kind in ("timeout", "response_reset", "text_only", "unverified_connect"):
        assert not failed_before_single_request(operation, network_error(kind))
    assert not failed_before_single_request("send_campaign_card", network_error("connect"))


@pytest.mark.parametrize("operation", NOTICE_OPERATIONS)
async def test_connector_failure_retries_same_notice_and_preserves_attempts(
    session_factory, operation
):
    row_id = await _seed_notice(session_factory, operation)
    gateway = Gateway(["connect"])
    worker = _worker(session_factory, gateway)
    assert await worker.process_once()
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        attempt = await session.scalar(select(DeliveryAttempt))
        assert row.state is OutboxState.RETRY_SCHEDULED
        assert row.gateway_started_at is None
        assert row.last_error == str(network_error("connect"))
        assert attempt.certainty is DeliveryCertainty.EXPLICIT_REJECTION
        assert row.run_at > utcnow()
        row.run_at = utcnow()
    assert render_health(await read_delivery_health(session_factory))[1] == 0
    assert await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempts = tuple(
            await session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number))
        )
        assert row.state is OutboxState.CONFIRMED
        assert [a.certainty for a in attempts] == [
            DeliveryCertainty.EXPLICIT_REJECTION,
            DeliveryCertainty.CONFIRMED,
        ]
        assert row.last_error is None
    assert gateway.calls == [operation, operation]


@pytest.mark.parametrize("operation", NOTICE_OPERATIONS)
async def test_connector_errors_retry_five_times_then_fail_health(session_factory, operation):
    row_id = await _seed_notice(session_factory, operation)
    gateway = Gateway(["connect"] * 6)
    worker = _worker(session_factory, gateway)
    for delay in (5, 15, 30, 60, 90, None):
        await _assert_failure_schedule(worker, session_factory, (row_id, delay))
    assert not await worker.process_once()
    assert len(gateway.calls) == 6
    async with session_factory() as session:
        attempts = tuple(await session.scalars(select(DeliveryAttempt)))
        assert len(attempts) == 6
        assert all(a.certainty is DeliveryCertainty.EXPLICIT_REJECTION for a in attempts)
    output, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "failed_notice_deliveries=1" in output


async def _assert_failure_schedule(worker, sessions, spec):
    row_id, delay = spec
    before = utcnow()
    assert await worker.process_once()
    after = utcnow()
    async with sessions() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        if delay is None:
            assert row.state is OutboxState.PRE_ACCEPT_FAILED
        else:
            assert row.state is OutboxState.RETRY_SCHEDULED
            assert (
                before + timedelta(seconds=delay) <= row.run_at <= after + timedelta(seconds=delay)
            )
            row.run_at = utcnow()


@pytest.mark.parametrize("operation", ["send_progress_notice", "send_lottery_query"])
async def test_rate_limit_does_not_consume_connection_retry_budget(session_factory, operation):
    row_id = await _seed_notice(session_factory, operation)
    rate_limit = TelegramRetryAfter(SendMessage(chat_id=-1001234, text="test"), "flood", 41)
    gateway = Gateway(["connect", rate_limit, *(["connect"] * 5)])
    worker = _worker(session_factory, gateway)
    for delay in (5, 41, 15, 30, 60, 90, None):
        await _assert_failure_schedule(worker, session_factory, (row_id, delay))
    assert len(gateway.calls) == 7
    assert not await worker.process_once()


@pytest.mark.parametrize("operation", ["send_progress_notice", "send_lottery_query"])
async def test_fifth_retry_can_confirm_delivery(session_factory, operation):
    row_id = await _seed_notice(session_factory, operation)
    gateway = Gateway(["connect"] * 5)
    worker = _worker(session_factory, gateway)
    for delay in (5, 15, 30, 60, 90):
        await _assert_failure_schedule(worker, session_factory, (row_id, delay))
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempts = tuple(
            await session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number))
        )
        assert row.state is OutboxState.CONFIRMED
        assert [a.certainty for a in attempts] == [DeliveryCertainty.EXPLICIT_REJECTION] * 5 + [
            DeliveryCertainty.CONFIRMED
        ]
    assert len(gateway.calls) == 6
    assert render_health(await read_delivery_health(session_factory))[1] == 0


@pytest.mark.parametrize("operation", ["send_temporary_reminder", "send_lottery_query"])
async def test_ambiguous_failure_after_safe_retry_is_held_without_replay(
    session_factory, operation
):
    row_id = await _seed_notice(session_factory, operation)
    gateway = Gateway(["connect", "timeout"])
    worker = _worker(session_factory, gateway)
    await _assert_failure_schedule(worker, session_factory, (row_id, 5))
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempts = tuple(
            await session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number))
        )
        assert row.state is OutboxState.UNKNOWN_HOLD
        assert [a.certainty for a in attempts] == [
            DeliveryCertainty.EXPLICIT_REJECTION,
            DeliveryCertainty.UNKNOWN,
        ]
    assert len(gateway.calls) == 2


@pytest.mark.parametrize("kind", ["timeout", "response_reset", "text_only", "unverified_connect"])
@pytest.mark.parametrize("operation", ["send_temporary_reminder", "send_lottery_query"])
async def test_ambiguous_notice_failure_remains_unknown_and_is_not_retried(
    session_factory, kind, operation
):
    row_id = await _seed_notice(session_factory, operation)
    gateway = Gateway([kind])
    worker = _worker(session_factory, gateway)
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempt = await session.scalar(select(DeliveryAttempt))
        assert row.state is OutboxState.UNKNOWN_HOLD
        assert attempt.certainty is DeliveryCertainty.UNKNOWN
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("operation", ["send_progress_notice", "send_temporary_reminder"])
async def test_history_reconciliation_preserves_original_unknown_without_resending(
    session_factory, operation
):
    row_id = await _seed_notice(session_factory, operation, unknown=True)
    options = _options(row_id)
    preview = await reconcile_notice_connection(session_factory, options)
    applied = await reconcile_notice_connection(
        session_factory, replace(options, expected_fingerprint=preview["fingerprint"])
    )
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempt = await session.scalar(select(DeliveryAttempt))
        audit = await session.scalar(select(AuditLog))
        assert row.state is OutboxState.PRE_ACCEPT_FAILED
        assert attempt.certainty is DeliveryCertainty.UNKNOWN
        assert row.last_error == attempt.error_description
        assert audit.details == applied
        assert applied["request_sent"] is False


async def test_verified_deletion_retry_preserves_history_and_confirms_same_target(session_factory):
    row_id = await _seed_reminder_deletion(session_factory)
    options = _options(row_id, retry_delete=True)
    preview = await reconcile_notice_connection(session_factory, options)
    assert preview["confirmed_reminder"]["message_id"] == 808
    applied = await reconcile_notice_connection(
        session_factory, replace(options, expected_fingerprint=preview["fingerprint"])
    )
    gateway = Gateway([])
    assert await _worker(session_factory, gateway).process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempts = tuple(
            await session.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.outbox_id == row_id)
                .order_by(DeliveryAttempt.attempt_number)
            )
        )
        audit = await session.scalar(select(AuditLog))
        assert row.state is OutboxState.CONFIRMED
        assert [a.certainty for a in attempts] == [
            DeliveryCertainty.UNKNOWN,
            DeliveryCertainty.CONFIRMED,
        ]
        assert audit.details == applied
    assert gateway.calls == ["delete_message"]
    assert gateway.requests[0].payload == {"message_id": 808}


async def test_rejected_deletion_retry_keeps_original_result_unknown(session_factory):
    row_id = await _seed_reminder_deletion(session_factory)
    options = _options(row_id, retry_delete=True)
    preview = await reconcile_notice_connection(session_factory, options)
    await reconcile_notice_connection(
        session_factory, replace(options, expected_fingerprint=preview["fingerprint"])
    )
    error = TelegramBadRequest(DeleteMessage(chat_id=-100123, message_id=808), "not found")
    worker = _worker(session_factory, Gateway([error]))
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempts = tuple(
            await session.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.outbox_id == row_id)
                .order_by(DeliveryAttempt.attempt_number)
            )
        )
        assert row.state is OutboxState.UNKNOWN_HOLD
        assert [a.certainty for a in attempts] == [
            DeliveryCertainty.UNKNOWN,
            DeliveryCertainty.EXPLICIT_REJECTION,
        ]


async def test_timed_out_deletion_retry_keeps_both_attempts_unknown(session_factory):
    row_id = await _seed_reminder_deletion(session_factory)
    options = _options(row_id, retry_delete=True)
    preview = await reconcile_notice_connection(session_factory, options)
    await reconcile_notice_connection(
        session_factory, replace(options, expected_fingerprint=preview["fingerprint"])
    )
    gateway = Gateway(["timeout"])
    worker = _worker(session_factory, gateway)
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempts = tuple(
            await session.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.outbox_id == row_id)
                .order_by(DeliveryAttempt.attempt_number)
            )
        )
        assert row.state is OutboxState.UNKNOWN_HOLD
        assert [a.certainty for a in attempts] == [
            DeliveryCertainty.UNKNOWN,
            DeliveryCertainty.UNKNOWN,
        ]
    assert gateway.calls == ["delete_message"]


async def test_history_reconciliation_requires_redirect_evidence_reference(session_factory):
    row_id = await _seed_notice(session_factory, "send_progress_notice", unknown=True)
    options = replace(_options(row_id), unredirected_evidence_ref="")
    with pytest.raises(ValueError, match="no-redirect"):
        await reconcile_notice_connection(session_factory, options)
    async with session_factory() as session:
        assert (await session.get(TelegramOutbox, row_id)).state is OutboxState.UNKNOWN_HOLD
        assert await session.scalar(select(AuditLog)) is None


@pytest.mark.parametrize(
    "mutation", ["timeout", "source_changed", "job_changed", "job_retimestamp", "too_old"]
)
async def test_deletion_retry_refuses_unverified_or_changed_evidence(session_factory, mutation):
    row_id = await _seed_reminder_deletion(session_factory)
    options = _options(row_id, retry_delete=True)
    preview = await reconcile_notice_connection(session_factory, options)
    async with session_factory() as session, session.begin():
        await _mutate_deletion_evidence(session, row_id, mutation)
    with pytest.raises(ValueError):
        await reconcile_notice_connection(
            session_factory, replace(options, expected_fingerprint=preview["fingerprint"])
        )
    async with session_factory() as session:
        assert (await session.get(TelegramOutbox, row_id)).state is OutboxState.UNKNOWN_HOLD
        assert await session.scalar(select(AuditLog)) is None


async def _mutate_deletion_evidence(session, row_id, mutation):
    row = await session.get(TelegramOutbox, row_id)
    source = await session.scalar(
        select(TelegramOutbox).where(TelegramOutbox.operation == "send_temporary_reminder")
    )
    job = await session.scalar(select(ScheduledJob))
    if mutation == "timeout":
        attempt = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == row_id)
        )
        row.last_error = attempt.error_description = "HTTP Client says - Request timeout error"
        return
    if mutation == "source_changed":
        source.payload = source.payload | {"delete_after_seconds": 30}
        return
    if mutation == "job_changed":
        job.payload = {"chat_id": row.recipient_chat_id, "message_id": 999}
        return
    if mutation == "job_retimestamp":
        job.updated_at = utcnow()
        return
    source.gateway_started_at = utcnow() - timedelta(hours=48)


def _options(row_id, *, retry_delete=False):
    return RepairOptions(
        outbox_id=row_id,
        runtime_ref=RUNTIME_REF,
        repair_ref="test",
        retry_delete=retry_delete,
        unredirected_evidence_ref="" if retry_delete else "test-no-redirect-evidence",
    )


async def _seed_notice(sessions, operation, *, unknown=False):
    async with sessions() as session, session.begin():
        group, campaign, participant = await seed_campaign(session)
        row = add_outbox(
            session,
            operation=operation,
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=group.chat_id,
            business_key=f"notice-test:{participant.id}:{operation}",
            payload={"text": "test notice"}
            if operation != "delete_message"
            else {"message_id": 808},
        )
        await session.flush()
        if unknown:
            _make_unknown(session, row)
        return row.id


def _make_unknown(session, row):
    row.state = OutboxState.UNKNOWN_HOLD
    row.gateway_started_at = utcnow()
    row.last_error = str(network_error("connect"))
    session.add(
        DeliveryAttempt(
            outbox_id=row.id,
            telegram_method=row.operation,
            attempt_number=1,
            certainty=DeliveryCertainty.UNKNOWN,
            error_code="TelegramNetworkError",
            error_description=row.last_error,
            finished_at=utcnow(),
        )
    )


async def _seed_reminder_deletion(sessions):
    async with sessions() as session, session.begin():
        group, campaign, participant = await seed_campaign(session)
        source = add_outbox(
            session,
            operation="send_temporary_reminder",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=group.chat_id,
            business_key=f"source-notice:{participant.id}",
            payload={"text": "test reminder", "delete_after_seconds": 15},
        )
        deletion = add_outbox(
            session,
            operation="delete_message",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=group.chat_id,
            business_key=f"delete_message:{group.chat_id}:808",
            payload={"message_id": 808},
        )
        await session.flush()
        _confirm_source_and_job(session, source, group.chat_id)
        _make_unknown(session, deletion)
        return deletion.id


def _confirm_source_and_job(session, source, chat_id):
    confirmed_at = utcnow() - timedelta(seconds=30)
    source.state = OutboxState.CONFIRMED
    source.gateway_started_at = confirmed_at - timedelta(seconds=1)
    source.projected_at = confirmed_at
    source.remote_message_id = 808
    session.add(
        DeliveryAttempt(
            outbox_id=source.id,
            telegram_method=source.operation,
            attempt_number=1,
            certainty=DeliveryCertainty.CONFIRMED,
            remote_result={"message_id": 808},
            finished_at=confirmed_at,
        )
    )
    session.add(
        ScheduledJob(
            job_type="delete_telegram_message",
            aggregate_id=source.aggregate_id,
            business_key=f"delete_message:{chat_id}:808",
            payload={"chat_id": chat_id, "message_id": 808},
            run_at=confirmed_at + timedelta(seconds=15),
            state=JobState.SUCCEEDED,
        )
    )
