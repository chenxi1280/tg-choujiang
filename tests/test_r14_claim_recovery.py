from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tests.test_r14_start_and_signup import _original_card
from tg_lottery.application.outbox_claims import OutboxClaimLost, lock_presend_claim
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.application.start_notices import (
    cancel_closed_start,
    project_start_notice,
    queue_start_notices,
    resume_start_notices,
)
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox
from tg_lottery.telegram.outbox_preflight import prepare_owned_delivery


def _claim(row):
    return SimpleNamespace(
        id=row.id,
        state=row.state,
        lease_owner=row.lease_owner,
        lease_until=row.lease_until,
        operation=row.operation,
        recipient_chat_id=row.recipient_chat_id,
        aggregate_id=row.aggregate_id,
        payload=dict(row.payload),
    )


@pytest.mark.parametrize("change", ["owner", "generation", "expired", "started", "confirmed"])
async def test_stale_preflight_cannot_overwrite_new_delivery(session_factory, change):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        row = add_outbox(
            session,
            operation="send_start_card",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=-100123,
            business_key="claim",
            payload={},
        )
        row.state, row.lease_owner, row.lease_until = (
            OutboxState.CLAIMED,
            "worker",
            utcnow() + timedelta(minutes=2),
        )
        await session.flush()
        old = _claim(row)
        if change == "owner":
            row.lease_owner = "replacement"
        if change == "generation":
            row.lease_until += timedelta(minutes=2)
        if change == "expired":
            row.lease_until = old.lease_until = utcnow() - timedelta(seconds=1)
        if change == "started":
            row.gateway_started_at = utcnow()
        if change == "confirmed":
            row.state, row.remote_message_id = OutboxState.CONFIRMED, 909
        expected = (
            row.state,
            row.lease_owner,
            row.lease_until,
            row.gateway_started_at,
            row.remote_message_id,
        )
    async with session_factory() as session, session.begin():
        with pytest.raises(OutboxClaimLost):
            await lock_presend_claim(session, old)
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, old.id)
        assert (
            row.state,
            row.lease_owner,
            row.lease_until,
            row.gateway_started_at,
            row.remote_message_id,
        ) == expected


async def test_lost_claim_stops_worker_without_starting_gateway():
    worker = SimpleNamespace(
        _prepare_gateway_call=AsyncMock(side_effect=OutboxClaimLost("stale")),
        _mark_gateway_started=AsyncMock(),
        _logger=SimpleNamespace(warning=AsyncMock()),
    )
    # Logging is synchronous in the worker.
    from unittest.mock import Mock

    worker._logger.warning = Mock()
    assert await prepare_owned_delivery(worker, SimpleNamespace(id="stale")) is None
    worker._mark_gateway_started.assert_not_called()
    worker._logger.warning.assert_called_once()


async def test_paused_page_projection_and_resume_keep_same_chain(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        await _original_card(session, campaign)
        await queue_start_notices(session, campaign)
        await session.flush()
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_start_card")
        )
        row.operation = "send_start_rules"
        row.payload = dict(row.payload) | {"start_pages": ["first"], "page_index": 0}
        row.state, row.remote_message_id = OutboxState.CONFIRMED, 901
        campaign.state = CampaignState.PAUSED
        assert await project_start_notice(session, row)
        await session.flush()
        next_row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_start_card")
        )
        next_row.state, next_row.lease_owner, next_row.lease_until = (
            OutboxState.CLAIMED,
            "worker",
            utcnow() + timedelta(minutes=2),
        )
        cid, next_id, first_id = campaign.id, next_row.id, row.id
        claim = _claim(next_row)
    assert await cancel_closed_start(session_factory, claim)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        intent = campaign.presentation["start_notice_intent"]
        next_row = await session.get(TelegramOutbox, next_id)
        assert next_row.state == OutboxState.RETRY_SCHEDULED and next_row.lease_owner is None
        campaign.state = CampaignState.RUNNING
        await resume_start_notices(session, campaign)
        await session.flush()
        assert campaign.presentation["start_notice_intent"] == intent
        assert next_row.run_at <= utcnow()
        first = await session.get(TelegramOutbox, first_id)
        assert first.state == OutboxState.CONFIRMED and first.remote_message_id == 901
