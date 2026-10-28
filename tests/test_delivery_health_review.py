from dataclasses import replace

import pytest
from sqlalchemy import select

from tests.unknown_review_helpers import frozen_request, seed_unknown
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import AuditLog, DeliveryAttempt, TelegramOutbox
from tg_lottery.ops import delivery_health
from tg_lottery.ops.delivery_health import read_delivery_health, render_health
from tg_lottery.ops.delivery_health_review import ReviewedDelivery, UnknownReviewStatus
from tg_lottery.ops.unknown_review import review_unknowns


async def acknowledge(sessions, ids):
    request = await frozen_request(sessions, ids)
    preview = await review_unknowns(sessions, request)
    await review_unknowns(sessions, replace(request, expected_fingerprint=preview["fingerprint"]))


async def test_unreviewed_fails_reviewed_warns_and_new_unknown_still_fails(session_factory):
    old_id = await seed_unknown(session_factory)
    before, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "unreviewed_unknown=1" in before
    await acknowledge(session_factory, [old_id])
    reviewed, status = render_health(await read_delivery_health(session_factory))
    assert status == 0 and "STATUS=DEGRADED_ACKNOWLEDGED" in reviewed
    assert "acknowledged_unknown=1" in reviewed and str(old_id) in reviewed
    assert "::warning" in reviewed and "remain UNKNOWN" in reviewed
    new_id = await seed_unknown(session_factory, "send_temporary_reminder")
    combined, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "STATUS=DEGRADED " in combined
    assert f"UNKNOWN_UNREVIEWED outbox_id={new_id}" in combined
    assert "acknowledged_unknown=1" in combined and "::warning" in combined


@pytest.mark.parametrize(
    "mutation", ["error", "payload", "cancelled", "attempt", "audit", "audit_ref", "audit_reason"]
)
async def test_changed_review_evidence_is_not_silently_acknowledged(session_factory, mutation):
    row_id = await seed_unknown(session_factory)
    await acknowledge(session_factory, [row_id])
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        await _mutate(session, row, mutation)
    output, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "invalidated_reviews=1" in output
    assert f"UNKNOWN_REVIEW_INVALIDATED outbox_id={row_id}" in output


async def _mutate(session, row, mutation):
    if mutation == "error":
        row.last_error = "changed"
    if mutation == "payload":
        row.payload = {"text": "changed"}
        row.request_hash = canonical_hash(row.payload)
    if mutation == "cancelled":
        row.state = OutboxState.CANCELLED
    if mutation == "attempt":
        attempt = await session.scalar(select(DeliveryAttempt))
        attempt.error_description = "changed"
    if mutation == "audit":
        audit = await session.scalar(select(AuditLog))
        audit.details = audit.details | {"snapshot_hash": "0" * 64}
    if mutation == "audit_ref":
        audit = await session.scalar(select(AuditLog))
        audit.details = audit.details | {"incident_ref": "changed"}
    if mutation == "audit_reason":
        audit = await session.scalar(select(AuditLog))
        audit.reason = "changed"


async def test_removed_review_returns_to_failure(session_factory):
    row_id = await seed_unknown(session_factory)
    await acknowledge(session_factory, [row_id])
    async with session_factory() as session, session.begin():
        await session.delete(await session.scalar(select(AuditLog)))
    output, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "unreviewed_unknown=1" in output


async def test_deleted_acknowledged_delivery_is_reported_as_invalid(session_factory):
    row_id = await seed_unknown(session_factory)
    await acknowledge(session_factory, [row_id])
    async with session_factory() as session, session.begin():
        await session.delete(await session.get(TelegramOutbox, row_id))
    output, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "invalidated_reviews=1" in output
    assert f"UNKNOWN_REVIEW_INVALIDATED outbox_id={row_id} operation=missing" in output


@pytest.mark.parametrize("valid_receipt", [False, True])
async def test_later_confirmation_does_not_prove_original_delivery(session_factory, valid_receipt):
    row_id = await seed_unknown(session_factory)
    await acknowledge(session_factory, [row_id])
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        row.state = OutboxState.CONFIRMED
        row.remote_message_id = 99
        row.projected_at = utcnow()
        result = {"message_id": 99, "chat": {"id": row.recipient_chat_id}}
        session.add(
            DeliveryAttempt(
                outbox_id=row_id,
                telegram_method=row.operation,
                attempt_number=2,
                certainty=DeliveryCertainty.CONFIRMED,
                remote_result=result if valid_receipt else {"message_id": 99},
                finished_at=utcnow(),
            )
        )
    output, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "invalidated_reviews=1" in output
    async with session_factory() as session:
        original = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.attempt_number == 1)
        )
        assert original.certainty is DeliveryCertainty.UNKNOWN


async def test_existing_failed_work_still_fails_with_acknowledged_unknown(session_factory):
    row_id = await seed_unknown(session_factory)
    await acknowledge(session_factory, [row_id])
    health = await read_delivery_health(session_factory)
    output, status = render_health(replace(health, failed_jobs=1))
    assert status == 1 and "failed_jobs=1" in output and "::warning" in output


async def test_failed_notice_fails_while_preserving_acknowledged_warning(session_factory):
    old_id = await seed_unknown(session_factory)
    await acknowledge(session_factory, [old_id])
    failed_id = await seed_unknown(session_factory, "send_temporary_reminder")
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, failed_id)
        row.state = OutboxState.PRE_ACCEPT_FAILED
        attempt = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == failed_id)
        )
        attempt.certainty = DeliveryCertainty.EXPLICIT_REJECTION
    output, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and "failed_notice_deliveries=1" in output
    assert f"UNKNOWN_ACKNOWLEDGED outbox_id={old_id}" in output and "::warning" in output


def test_summary_sanitizes_untrusted_operation_names():
    item = ReviewedDelivery("00000000-0000-0000-0000-000000000001", "x\n::notice::injected")
    reviews = UnknownReviewStatus((), (item,), (), 1)
    health = delivery_health.DeliveryHealth(reviews, 0, 0, 0, 0, 0)
    output, status = render_health(health)
    assert status == 1 and "operation=invalid" in output
    assert "::notice::injected" not in output


@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError])
def test_health_error_does_not_leak_exception_details(monkeypatch, capsys, error_type):
    async def broken():
        raise error_type("sensitive connection detail")

    monkeypatch.setattr(delivery_health, "_run", broken)
    assert delivery_health.main() == 2
    captured = capsys.readouterr().out
    assert "delivery_health_error" in captured and "sensitive connection detail" not in captured
