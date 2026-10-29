from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_notice_deletion import NoticeBot, _delivery_worker, _seed_notice
from tg_lottery.application.message_deletion import project_message_deletion
from tg_lottery.application.records import utcnow
from tg_lottery.application.retention_policy import (
    POLICY_KEY,
    activate_retention,
    cancel_retired_deletion,
    progress_retired,
)
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import (
    Campaign,
    DeliveryAttempt,
    RuntimePolicy,
    ScheduledJob,
    TelegramOutbox,
)


async def test_old_activity_new_progress_ignores_previous_fifteen_seconds(session_factory):
    identifier = await _seed_notice(session_factory)
    await activate_retention(session_factory)
    bot = NoticeBot()
    assert await _delivery_worker(session_factory, bot).process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, identifier)
        job = await session.scalar(select(ScheduledJob))
        attempt = await session.scalar(select(DeliveryAttempt))
        assert row.payload["delete_after_seconds"] == 20
        assert job.run_at == attempt.finished_at + timedelta(seconds=20)
        assert len(bot.sent) == 1


@pytest.mark.parametrize("seconds", [None, 15, 20, 60])
async def test_new_activity_respects_explicit_retention_setting(session_factory, seconds):
    identifier = await _seed_notice(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.scalar(select(Campaign))
        campaign.presentation = {
            "ux_policy_version": "r14",
            "completion_delete_after_seconds": seconds,
        }
    await activate_retention(session_factory)
    assert await _delivery_worker(session_factory, NoticeBot()).process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, identifier)
        job = await session.scalar(select(ScheduledJob))
        assert row.payload.get("delete_after_seconds") == seconds
        assert (job is None) == (seconds is None)


async def test_activation_is_stable_and_pre_activation_progress_is_not_deleted(session_factory):
    identifier = await _seed_notice(session_factory)
    assert await _delivery_worker(session_factory, NoticeBot()).process_once()
    await activate_retention(session_factory)
    async with session_factory() as session, session.begin():
        policy = await session.get(RuntimePolicy, POLICY_KEY)
        policy.activated_at = utcnow() + timedelta(seconds=2)
        activation = policy.activated_at
    await activate_retention(session_factory)
    async with session_factory() as session:
        assert (await session.get(RuntimePolicy, POLICY_KEY)).activated_at == activation
        row = await session.get(TelegramOutbox, identifier)
        assert await progress_retired(session, row)


@pytest.mark.parametrize("offset,expected", [(-5, 0), (5, 1)])
async def test_inflight_receipt_uses_actual_send_time_not_ack_time(
    session_factory, offset, expected
):
    identifier = await _seed_notice(session_factory)
    await activate_retention(session_factory)
    async with session_factory() as session, session.begin():
        policy = await session.get(RuntimePolicy, POLICY_KEY)
        row = await session.get(TelegramOutbox, identifier)
        row.payload = {
            key: value for key, value in row.payload.items() if key != "delete_after_seconds"
        }
        row.state = OutboxState.CONFIRMED
        row.remote_message_id = 989
        row.gateway_started_at = policy.activated_at - timedelta(seconds=10)
        session.add(
            DeliveryAttempt(
                outbox_id=row.id,
                attempt_number=1,
                telegram_method=row.operation,
                certainty=DeliveryCertainty.CONFIRMED,
                finished_at=utcnow() + timedelta(seconds=20),
                remote_result={
                    "date": (policy.activated_at + timedelta(seconds=offset)).timestamp()
                },
            )
        )
        await session.flush()
        await project_message_deletion(session, row)
        await session.flush()
        jobs = list(await session.scalars(select(ScheduledJob)))
        assert len(jobs) == expected
        if jobs:
            assert row.payload["delete_after_seconds"] == 20


async def test_unknown_actual_send_does_not_guess_a_deletion(session_factory):
    identifier = await _seed_notice(session_factory)
    await activate_retention(session_factory)
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, identifier)
        with pytest.raises(ValueError, match="send time unknown"):
            await progress_retired(session, row)


async def test_old_pending_delete_is_cancelled_before_telegram_call(session_factory):
    from tg_lottery.application.records import add_outbox

    identifier = await _seed_notice(session_factory)
    assert await _delivery_worker(session_factory, NoticeBot()).process_once()
    await activate_retention(session_factory)
    async with session_factory() as session, session.begin():
        policy = await session.get(RuntimePolicy, POLICY_KEY)
        policy.activated_at = utcnow() + timedelta(seconds=2)
        source = await session.get(TelegramOutbox, identifier)
        deletion = add_outbox(
            session,
            operation="delete_message",
            aggregate_type="participant",
            aggregate_id=source.aggregate_id,
            recipient_chat_id=source.recipient_chat_id,
            business_key="old-delete",
            payload={"message_id": source.remote_message_id},
        )
        deletion.state = OutboxState.CLAIMED
        deletion.lease_owner = "preflight-worker"
        deletion.lease_until = utcnow() + timedelta(minutes=2)
        await session.flush()
    assert await cancel_retired_deletion(session_factory, deletion)
    async with session_factory() as session:
        assert (await session.get(TelegramOutbox, deletion.id)).state == OutboxState.CANCELLED
