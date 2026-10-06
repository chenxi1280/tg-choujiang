from dataclasses import replace

import pytest
from sqlalchemy import select

from tests.test_invite_connection_failures import _seed_request, network_error
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import AuditLog, DeliveryAttempt, TelegramOutbox
from tg_lottery.ops.reconcile_invite_connection import RepairOptions, reconcile_invite_connection

RUNTIME_REF = "045cf378117117574bea371a6befd74e8852fb1e:aiogram=3.31.0:aiohttp=3.14.3"


async def test_repair_preserves_original_unknown_attempt_and_records_audit(session_factory) -> None:
    options = await _seed_unknown(session_factory)
    report = await reconcile_invite_connection(session_factory, options)
    async with session_factory() as session:
        assert (
            await session.get(TelegramOutbox, options.outbox_id)
        ).state is OutboxState.UNKNOWN_HOLD
        assert await session.scalar(select(AuditLog)) is None
    applied = await reconcile_invite_connection(
        session_factory, replace(options, expected_fingerprint=report["fingerprint"])
    )
    assert applied["applied"] and applied["request_sent"] is False
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, options.outbox_id)
        attempts = tuple(await session.scalars(select(DeliveryAttempt)))
        audit = await session.scalar(select(AuditLog))
        assert row.state is OutboxState.PRE_ACCEPT_FAILED
        assert row.last_error == str(network_error("connect"))
        assert row.remote_message_id is None and row.encrypted_remote_secret is None
        assert len(attempts) == 1 and attempts[0].certainty is DeliveryCertainty.UNKNOWN
        assert audit.action == "outbox.connect_failure_reconciled"
        assert audit.details == applied


async def test_stale_preview_cannot_write_audit_or_change_state(session_factory) -> None:
    options = await _seed_unknown(session_factory)
    report = await reconcile_invite_connection(session_factory, options)
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, options.outbox_id)
        row.updated_at = utcnow()
    with pytest.raises(ValueError, match="preview changed"):
        await reconcile_invite_connection(
            session_factory, replace(options, expected_fingerprint=report["fingerprint"])
        )
    async with session_factory() as session:
        assert (
            await session.get(TelegramOutbox, options.outbox_id)
        ).state is OutboxState.UNKNOWN_HOLD
        assert await session.scalar(select(AuditLog)) is None


async def test_a_later_connect_failure_cannot_resolve_an_earlier_unknown_write(session_factory):
    options = await _seed_unknown(session_factory)
    async with session_factory() as session, session.begin():
        attempt = await session.scalar(select(DeliveryAttempt))
        session.add(
            DeliveryAttempt(
                outbox_id=options.outbox_id,
                telegram_method=attempt.telegram_method,
                attempt_number=2,
                certainty=DeliveryCertainty.UNKNOWN,
                error_code=attempt.error_code,
                error_description=attempt.error_description,
                finished_at=utcnow(),
            )
        )
    with pytest.raises(ValueError, match="another delivery attempt"):
        await reconcile_invite_connection(session_factory, options)


async def test_repeated_apply_cannot_add_another_audit(session_factory):
    options = await _seed_unknown(session_factory)
    report = await reconcile_invite_connection(session_factory, options)
    apply_options = replace(options, expected_fingerprint=report["fingerprint"])
    await reconcile_invite_connection(session_factory, apply_options)
    with pytest.raises(ValueError, match="awaiting gateway reconciliation"):
        await reconcile_invite_connection(session_factory, apply_options)
    async with session_factory() as session:
        assert len(tuple(await session.scalars(select(AuditLog)))) == 1


@pytest.mark.parametrize(
    "mutation",
    ["response_timeout", "other_operation", "lease", "remote_evidence", "request_changed"],
)
async def test_repair_rejects_unsupported_or_changed_evidence(session_factory, mutation) -> None:
    options = await _seed_unknown(session_factory)
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, options.outbox_id)
        await _mutate_evidence(session, row, mutation)
    with pytest.raises(ValueError):
        await reconcile_invite_connection(session_factory, options)
    async with session_factory() as session:
        assert (
            await session.get(TelegramOutbox, options.outbox_id)
        ).state is OutboxState.UNKNOWN_HOLD
        assert await session.scalar(select(AuditLog)) is None


async def _mutate_evidence(session, row, mutation):
    if mutation == "response_timeout":
        attempt = await session.scalar(select(DeliveryAttempt))
        attempt.error_description = row.last_error = "HTTP Client says - Request timeout error"
        return
    if mutation == "other_operation":
        row.operation = "send_campaign_card"
        return
    if mutation == "lease":
        row.lease_owner = "another-worker"
        return
    if mutation == "remote_evidence":
        row.remote_message_id = 123
        return
    if mutation == "request_changed":
        row.payload = row.payload | {"name": "changed"}
        return
    raise ValueError("unknown test mutation")


async def _seed_unknown(sessions) -> RepairOptions:
    row_id = await _seed_request(sessions)
    async with sessions() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        row.state = OutboxState.UNKNOWN_HOLD
        row.gateway_started_at = utcnow()
        row.last_error = str(network_error("connect"))
        session.add(
            DeliveryAttempt(
                outbox_id=row_id,
                telegram_method=row.operation,
                attempt_number=1,
                certainty=DeliveryCertainty.UNKNOWN,
                error_code="TelegramNetworkError",
                error_description=row.last_error,
                finished_at=utcnow(),
            )
        )
    return RepairOptions(outbox_id=row_id, runtime_ref=RUNTIME_REF, repair_ref="test-incident")
