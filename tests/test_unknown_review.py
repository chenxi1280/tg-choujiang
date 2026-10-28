from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from tests.unknown_review_helpers import frozen_request, seed_unknown
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import AuditLog, DeliveryAttempt, TelegramOutbox
from tg_lottery.ops.delivery_health import read_delivery_health, render_health
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
