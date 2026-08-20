import base64
import os
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.draw_commitments import commitment_hash, ensure_draw_commitment
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.operational_blocks import (
    record_outbox_target_block,
    resolve_outbox_target_block,
)
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState, OutboxState, QualificationState, TicketSource
from tg_lottery.domain.errors import SettlementBlocked
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignOperationalBlock,
    CampaignParticipationGroup,
    DrawCommitment,
    PollingOffset,
    QualificationCheck,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.outbox import OutboxWorker


class ConfirmingGateway:
    async def execute(self, outbox, invite_link=None):
        return GatewayResult(remote_result={"ok": True}, remote_message_id=9901)


async def test_v24_draw_waits_for_public_commitment_then_reveals_same_seed(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    campaign_id = await _seed_v24_case(session_factory, now)
    uow_factory = partial(UnitOfWork, session_factory)
    settlement = SettlementService(uow_factory)

    with pytest.raises(SettlementBlocked, match="draw_commitment_delivery_pending"):
        await settlement.settle(campaign_id, now)

    async with session_factory() as session, session.begin():
        outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_draw_commitment")
        )
        assert outbox is not None
        outbox.state = OutboxState.CONFIRMED
        outbox.remote_message_id = 8801

    snapshot = await settlement.settle(campaign_id, now)
    draw_run = await DrawingService(uow_factory).run(campaign_id, actor_user_id=101)

    async with session_factory() as session:
        commitment = await session.scalar(
            select(DrawCommitment).where(DrawCommitment.campaign_id == campaign_id)
        )
        result = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_draw_result")
        )
    assert commitment is not None and commitment.revealed_at is not None
    assert commitment.commitment_hash == commitment_hash(campaign_id, commitment.seed)
    assert draw_run.random_input == commitment.seed
    assert result is not None
    assert result.payload["revealed_seed"] == commitment.seed
    assert result.payload["commitment_hash"] == commitment.commitment_hash
    assert result.payload["snapshot_hash"] == snapshot.snapshot_hash


async def test_draw_commitment_creation_is_idempotent_per_campaign(session_factory) -> None:
    now = datetime.now(UTC)
    campaign_id = await _seed_v24_case(session_factory, now)

    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        first = await ensure_draw_commitment(session, campaign, now)
        second = await ensure_draw_commitment(session, campaign, now + timedelta(seconds=1))
        count = await session.scalar(select(func.count(DrawCommitment.id)))

    assert first is not None and second is not None
    assert first.id == second.id
    assert count == 1


async def test_commitment_restores_target_from_confirmed_card_fact(session_factory) -> None:
    now = datetime.now(UTC)
    campaign_id = await _seed_v24_case(session_factory, now)
    async with session_factory() as session, session.begin():
        target = await session.scalar(
            select(CampaignParticipationGroup).where(
                CampaignParticipationGroup.campaign_id == campaign_id
            )
        )
        assert target is not None
        target.publication_state = "failed"
        session.add(_confirmed_campaign_card(campaign_id, target.chat_id_snapshot, now))

    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        await ensure_draw_commitment(session, campaign, now)
        target = await session.scalar(
            select(CampaignParticipationGroup).where(
                CampaignParticipationGroup.campaign_id == campaign_id
            )
        )
        assert target is not None and target.publication_state == "confirmed"


async def test_resolving_confirmed_card_block_does_not_mark_target_failed(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    campaign_id = await _seed_v24_case(session_factory, now)
    async with session_factory() as session, session.begin():
        target = await session.scalar(
            select(CampaignParticipationGroup).where(
                CampaignParticipationGroup.campaign_id == campaign_id
            )
        )
        assert target is not None
        row = _confirmed_campaign_card(campaign_id, target.chat_id_snapshot, now)
        session.add(row)
        await session.flush()
        await resolve_outbox_target_block(session, row)
        assert target.publication_state == "confirmed"


@pytest.mark.parametrize(
    "delivery_state", [OutboxState.PRE_ACCEPT_FAILED, OutboxState.UNKNOWN_HOLD]
)
async def test_admin_can_retry_failed_or_unknown_commitment_delivery(
    session_factory, delivery_state
) -> None:
    campaign_id = await _seed_v24_case(session_factory, datetime.now(UTC))
    async with session_factory() as session, session.begin():
        outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_draw_commitment")
        )
        assert outbox is not None
        outbox.state = delivery_state
        outbox.gateway_started_at = datetime.now(UTC)

    count = await CampaignService(
        partial(UnitOfWork, session_factory)
    ).retry_failed_draw_commitments(campaign_id, 101)

    async with session_factory() as session:
        outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_draw_commitment")
        )
    assert count == 1
    assert outbox is not None and outbox.state is OutboxState.PENDING
    assert outbox.gateway_started_at is None


async def test_confirmed_commitment_retry_resolves_its_operational_block(
    session_factory,
) -> None:
    campaign_id = await _seed_v24_case(session_factory, datetime.now(UTC))
    async with session_factory() as session, session.begin():
        outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_draw_commitment")
        )
        assert outbox is not None
        await record_outbox_target_block(
            session, outbox, error_code="test_failure", details="retrying"
        )

    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    worker = OutboxWorker(
        owner_id="commitment-test-worker",
        session_factory=session_factory,
        gateway=ConfirmingGateway(),
        cipher=SecretCipher(key),
    )
    assert await worker.process_once() is True

    async with session_factory() as session:
        block = await session.scalar(
            select(CampaignOperationalBlock).where(
                CampaignOperationalBlock.campaign_id == campaign_id
            )
        )
    assert block is not None and block.resolved_at is not None


async def _seed_v24_case(session_factory, now: datetime):
    cutoff = now - timedelta(minutes=10)
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(
            session, state=CampaignState.SETTLING, cutoff_at=cutoff
        )
        campaign.contract_version = "v2.4"
        campaign.published_message_id = 7001
        session.add(
            CampaignParticipationGroup(
                campaign_id=campaign.id,
                group_id=group.id,
                position=1,
                is_primary=True,
                chat_id_snapshot=group.chat_id,
                title_snapshot=group.title,
                publication_state="confirmed",
                published_message_id=7001,
                published_at=now,
            )
        )
        await session.flush()
        await ensure_draw_commitment(session, campaign, now)
        session.add(_draw_check(participant.id, now))
        session.add(_polling(now))
        session.add(_ticket(campaign.id, participant.id, cutoff))
        return campaign.id


def _draw_check(participant_id, now: datetime) -> QualificationCheck:
    return QualificationCheck(
        participant_id=participant_id,
        phase="draw",
        state=QualificationState.ELIGIBLE,
        observed_status="member",
        observed_at=now,
    )


def _polling(now: datetime) -> PollingOffset:
    return PollingOffset(
        bot_id=123,
        last_durable_update_id=50,
        last_poll_succeeded_at=now,
        updated_at=now,
    )


def _ticket(campaign_id, participant_id, cutoff: datetime) -> TicketLedger:
    return TicketLedger(
        campaign_id=campaign_id,
        participant_id=participant_id,
        delta=1,
        source_type=TicketSource.REGISTRATION,
        source_ref="v24-seed",
        business_key="v24-seed-ticket",
        occurred_at=cutoff,
    )


def _confirmed_campaign_card(campaign_id, chat_id: int, now: datetime) -> TelegramOutbox:
    return TelegramOutbox(
        operation="send_campaign_card",
        aggregate_type="campaign",
        aggregate_id=str(campaign_id),
        recipient_chat_id=chat_id,
        business_key=f"confirmed-card:{campaign_id}",
        request_hash="c" * 64,
        payload={},
        run_at=now,
        state=OutboxState.CONFIRMED,
        remote_message_id=9902,
        updated_at=now,
    )
