from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import DeliveryAttempt, TelegramOutbox
from tg_lottery.ops.reconcile_legacy_outbox import (
    ApplyOptions,
    ReconcileWindow,
    apply_reconciliation,
    preview,
)


async def test_preview_fingerprints_only_legacy_exception_group_unknowns(session_factory) -> None:
    window = _window()
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        await _unknown_outbox(session, campaign_id=str(campaign.id), created_at=window.start_at)
        await _network_unknown_outbox(
            session, campaign_id=str(campaign.id), created_at=window.start_at
        )

    report = await preview(session_factory, window)

    assert len(report.candidates) == 1
    assert report.candidates[0].operation == "edit_campaign_card_state"
    assert len(report.fingerprint) == 64


async def test_apply_closes_legacy_unknown_without_rewriting_attempt_certainty(
    session_factory,
) -> None:
    window = _window()
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        outbox_id = await _unknown_outbox(
            session, campaign_id=str(campaign.id), created_at=window.start_at
        )
    report = await preview(session_factory, window)
    options = ApplyOptions(
        window=window,
        expected_fingerprint=report.fingerprint,
        expected_count=1,
        repair_ref="test-run",
    )

    applied = await apply_reconciliation(session_factory, options)

    assert applied.applied is True
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, outbox_id)
        attempt = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == outbox_id)
        )
    assert row is not None
    assert row.state is OutboxState.CANCELLED
    assert row.last_error is not None and "test-run" in row.last_error
    assert attempt is not None
    assert attempt.certainty is DeliveryCertainty.UNKNOWN


async def _unknown_outbox(session, *, campaign_id: str, created_at: datetime):
    row = TelegramOutbox(
        operation="edit_campaign_card_state",
        aggregate_type="campaign",
        aggregate_id=campaign_id,
        recipient_chat_id=-100123,
        business_key=f"legacy-edit:{campaign_id}",
        request_hash="a" * 64,
        payload={"message_id": 42, "has_cover": False},
        state=OutboxState.UNKNOWN_HOLD,
        run_at=created_at,
        gateway_started_at=created_at,
        last_error="unhandled errors in a TaskGroup (1 sub-exception)",
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(row)
    await session.flush()
    session.add(
        DeliveryAttempt(
            outbox_id=row.id,
            attempt_number=1,
            telegram_method=row.operation,
            certainty=DeliveryCertainty.UNKNOWN,
            error_code="ExceptionGroup",
            error_description=row.last_error,
            finished_at=created_at,
        )
    )
    return row.id


async def _network_unknown_outbox(session, *, campaign_id: str, created_at: datetime) -> None:
    row = TelegramOutbox(
        operation="send_invite_link",
        aggregate_type="invite_link",
        aggregate_id=campaign_id,
        recipient_chat_id=201,
        business_key=f"network-send:{campaign_id}",
        request_hash="b" * 64,
        payload={"invite_link_id": campaign_id},
        state=OutboxState.UNKNOWN_HOLD,
        run_at=created_at,
        gateway_started_at=created_at,
        last_error="HTTP timeout",
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(row)
    await session.flush()
    session.add(
        DeliveryAttempt(
            outbox_id=row.id,
            attempt_number=1,
            telegram_method=row.operation,
            certainty=DeliveryCertainty.UNKNOWN,
            error_code="TimeoutError",
            error_description=row.last_error,
            finished_at=created_at,
        )
    )


def _window() -> ReconcileWindow:
    start = datetime(2026, 8, 15, 13, 0, tzinfo=UTC)
    return ReconcileWindow(start_at=start, end_at=start + timedelta(hours=1))
