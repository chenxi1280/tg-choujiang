import json
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from tests.test_notice_connection_recovery import _seed_reminder_deletion
from tests.unknown_review_helpers import frozen_request, seed_unknown
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import AuditLog, DeliveryAttempt, ScheduledJob, TelegramOutbox
from tg_lottery.ops.delivery_health import read_delivery_health, render_health
from tg_lottery.ops.reconcile_notice_deletion import verified_expired_deletion_source
from tg_lottery.ops.unknown_review import read_manifest, review_unknowns


async def test_review_is_previewed_then_audited_without_changing_delivery(session_factory):
    ids = [
        await seed_unknown(session_factory, op)
        for op in ("send_progress_notice", "send_temporary_reminder")
    ]
    request = await frozen_request(session_factory, ids)
    preview = await review_unknowns(session_factory, request)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
    applied = await review_unknowns(
        session_factory, replace(request, expected_fingerprint=preview["fingerprint"])
    )
    repeated = await review_unknowns(
        session_factory, replace(request, expected_fingerprint=preview["fingerprint"])
    )
    assert applied["applied"] and repeated["fingerprint"] == preview["fingerprint"]
    async with session_factory() as session:
        rows = tuple(await session.scalars(select(TelegramOutbox)))
        attempts = tuple(await session.scalars(select(DeliveryAttempt)))
        audits = tuple(await session.scalars(select(AuditLog)))
        assert all(row.state is OutboxState.UNKNOWN_HOLD for row in rows)
        assert all(attempt.certainty is DeliveryCertainty.UNKNOWN for attempt in attempts)
        assert len(audits) == 2
        assert all(a.details["delivery_result"] == "unknown" for a in audits)


async def test_changed_target_aborts_entire_review(session_factory):
    ids = [await seed_unknown(session_factory), await seed_unknown(session_factory)]
    request = await frozen_request(session_factory, ids)
    preview = await review_unknowns(session_factory, request)
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, ids[1])
        row.last_error = "changed"
    with pytest.raises(ValueError):
        await review_unknowns(
            session_factory, replace(request, expected_fingerprint=preview["fingerprint"])
        )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert all(
            row.state is OutboxState.UNKNOWN_HOLD
            for row in await session.scalars(select(TelegramOutbox))
        )


async def test_review_refuses_new_attempt_or_wrong_preview(session_factory):
    row_id = await seed_unknown(session_factory)
    request = await frozen_request(session_factory, [row_id])
    preview = await review_unknowns(session_factory, request)
    with pytest.raises(ValueError, match="preview changed"):
        await review_unknowns(session_factory, replace(request, expected_fingerprint="0" * 64))
    async with session_factory() as session, session.begin():
        original = await session.scalar(select(DeliveryAttempt))
        session.add(
            DeliveryAttempt(
                outbox_id=row_id,
                telegram_method=original.telegram_method,
                attempt_number=2,
                certainty=DeliveryCertainty.UNKNOWN,
                error_code="TelegramNetworkError",
                error_description="another call",
                finished_at=original.finished_at,
            )
        )
    with pytest.raises(ValueError, match="attempt count"):
        await review_unknowns(
            session_factory, replace(request, expected_fingerprint=preview["fingerprint"])
        )


async def test_new_unknown_during_review_is_not_acknowledged(session_factory):
    approved_id = await seed_unknown(session_factory)
    request = await frozen_request(session_factory, [approved_id])
    preview = await review_unknowns(session_factory, request)
    new_id = await seed_unknown(session_factory, "send_temporary_reminder")
    await review_unknowns(
        session_factory, replace(request, expected_fingerprint=preview["fingerprint"])
    )
    output, status = render_health(await read_delivery_health(session_factory))
    assert status == 1 and f"UNKNOWN_UNREVIEWED outbox_id={new_id}" in output
    assert f"UNKNOWN_ACKNOWLEDGED outbox_id={approved_id}" in output


async def test_review_lock_timeout_leaves_no_partial_audit(session_factory):
    row_id = await seed_unknown(session_factory)
    request = await frozen_request(session_factory, [row_id])
    preview = await review_unknowns(session_factory, request)
    async with session_factory() as holder, holder.begin():
        await holder.scalar(
            select(TelegramOutbox).where(TelegramOutbox.id == row_id).with_for_update()
        )
        with pytest.raises(DBAPIError):
            await review_unknowns(
                session_factory, replace(request, expected_fingerprint=preview["fingerprint"])
            )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert (await session.get(TelegramOutbox, row_id)).state is OutboxState.UNKNOWN_HOLD


def test_manifest_rejects_duplicate_ids():
    line = b'{"outbox_id":"00000000-0000-0000-0000-000000000001"}\n'
    with pytest.raises(ValueError, match="distinct"):
        read_manifest(line + line)


async def test_live_reminder_deletion_is_not_reviewable(session_factory):
    row_id = await _seed_reminder_deletion(session_factory)
    request = await frozen_request(session_factory, [row_id])
    with pytest.raises(ValueError, match="still within the deletion window"):
        await review_unknowns(session_factory, request)


@pytest.mark.parametrize("change_source", [False, True])
async def test_expired_deletion_review_preserves_unknown_attempt(session_factory, change_source):
    row_id = await _seed_reminder_deletion(session_factory)
    await _expire_reminder(session_factory, row_id)
    request = await frozen_request(session_factory, [row_id])
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, row_id)
        source = await verified_expired_deletion_source(session, row)
    manifest, digest = read_manifest(
        json.dumps(request.manifest[0] | {"deletion_source": source}).encode()
    )
    request = replace(request, manifest=manifest, manifest_sha256=digest)
    preview = await review_unknowns(session_factory, request)
    applied = replace(request, expected_fingerprint=preview["fingerprint"])
    if change_source:
        async with session_factory() as session, session.begin():
            job = await session.scalar(select(ScheduledJob))
            job.payload = job.payload | {"source_outbox_id": str(row_id)}
        with pytest.raises(ValueError, match="job target does not match"):
            await review_unknowns(session_factory, applied)
    else:
        await review_unknowns(session_factory, applied)
    async with session_factory() as session:
        deletion = await session.get(TelegramOutbox, row_id)
        attempt = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == row_id)
        )
        audit = await session.scalar(select(AuditLog))
        assert deletion.state is OutboxState.UNKNOWN_HOLD
        assert attempt.certainty is DeliveryCertainty.UNKNOWN and attempt.remote_result is None
        if change_source:
            assert audit is None
        else:
            assert audit.details["deletion_source"] == source
            assert audit.details["delivery_result"] == "unknown"
    _, status = render_health(await read_delivery_health(session_factory))
    assert status == int(change_source)


async def _expire_reminder(sessions, row_id):
    async with sessions() as session, session.begin():
        deletion = await session.get(TelegramOutbox, row_id)
        source = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_temporary_reminder")
        )
        confirmation = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == source.id)
        )
        confirmed_at = utcnow() - timedelta(hours=49)
        source.gateway_started_at = confirmed_at - timedelta(seconds=1)
        source.projected_at = confirmed_at
        source.payload = source.payload | {"delete_after_seconds": 20}
        source.request_hash = canonical_hash(source.payload)
        confirmation.finished_at = confirmed_at
        job = await session.scalar(select(ScheduledJob))
        job.run_at = confirmed_at + timedelta(seconds=20)
        job.payload = job.payload | {"source_outbox_id": str(source.id)}
        deletion.gateway_started_at = job.run_at + timedelta(seconds=1)
