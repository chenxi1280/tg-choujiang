from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.application.queries import QueryService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    QualificationState,
    TicketSource,
    UpdateState,
)
from tg_lottery.domain.errors import InvitationValidationDeferred, SettlementBlocked
from tg_lottery.infrastructure.db import (
    DrawEntry,
    DrawSnapshot,
    Invitation,
    InviteLink,
    PollingOffset,
    QualificationCheck,
    TelegramOutbox,
    TelegramUpdate,
    TicketLedger,
    Winner,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_invitation_first_attribution_and_reward_are_idempotent(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session, cutoff_at=now + timedelta(days=2))
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"link").hexdigest(),
            telegram_name="lot_test",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        link_id = link.id
        campaign_id = campaign.id
    service = InvitationService(partial(UnitOfWork, session_factory))
    observation = JoinObservation(link_id, 301, now)
    invitation = await service.record_joined(observation)
    assert invitation is not None
    attempt = await service.begin_validation(
        invitation.id,
        observed_at=now + timedelta(hours=24),
    )
    assert attempt.requires_observation is True
    async with session_factory() as session:
        checking = await session.get(type(invitation), invitation.id)
        assert checking.state is InvitationState.CHECKING
    validated = await service.complete_validation(
        invitation.id,
        observed_at=now + timedelta(hours=24),
        is_current_member=True,
    )
    await service.validate(
        invitation.id,
        observed_at=now + timedelta(hours=25),
        is_current_member=True,
    )
    async with session_factory() as session:
        ledger_count = await session.scalar(
            select(func.count(TicketLedger.id)).where(
                TicketLedger.campaign_id == campaign_id,
                TicketLedger.source_type == TicketSource.INVITATION,
            )
        )
        assert validated.state is InvitationState.VALID
        assert ledger_count == 1


async def test_invitation_reward_is_deferred_while_campaign_paused(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session,
            state=CampaignState.PAUSED,
            cutoff_at=now + timedelta(days=2),
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"paused-link").hexdigest(),
            telegram_name="lot_paused",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        link_id = link.id
    service = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await service.record_joined(JoinObservation(link_id, 302, now))
    assert invitation is not None
    with pytest.raises(InvitationValidationDeferred):
        await service.validate(
            invitation.id,
            observed_at=now + timedelta(hours=24),
            is_current_member=True,
        )


async def test_self_invitation_is_rejected(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"self-link").hexdigest(),
            telegram_name="lot_self",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        link_id = link.id
        inviter_user_id = participant.user_id
    service = InvitationService(partial(UnitOfWork, session_factory))
    assert await service.record_join_request(JoinObservation(link_id, inviter_user_id, now)) is None
    assert await service.record_joined(JoinObservation(link_id, inviter_user_id, now)) is None
    async with session_factory() as session:
        assert await session.scalar(select(func.count(Invitation.id))) == 0


async def test_settlement_ignores_unrelated_group_failed_update(session_factory) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=10)
    campaign_id, participant_id = await _seed_settlement_case(
        session_factory, now=now, cutoff=cutoff
    )
    async with session_factory() as session, session.begin():
        session.add(
            QualificationCheck(
                participant_id=participant_id,
                phase="draw",
                state=QualificationState.ELIGIBLE,
                observed_status="not_required",
                observed_at=now,
            )
        )
        session.add(
            TelegramUpdate(
                bot_id=123,
                update_id=49,
                update_type="message",
                routing_chat_id=-100999,
                event_at=cutoff - timedelta(minutes=1),
                payload={"update_id": 49},
                payload_hash="9" * 64,
                state=UpdateState.FAILED,
                attempts=1,
            )
        )
    snapshot = await SettlementService(partial(UnitOfWork, session_factory)).settle(
        campaign_id, now
    )
    assert snapshot.campaign_id == campaign_id


async def test_settlement_blocks_without_draw_qualification_then_draws_once(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=10)
    campaign_id, participant_id = await _seed_settlement_case(
        session_factory, now=now, cutoff=cutoff
    )
    uow_factory = partial(UnitOfWork, session_factory)
    settlement = SettlementService(uow_factory)
    with pytest.raises(SettlementBlocked, match="qualification_review_pending"):
        await settlement.settle(campaign_id, now)
    async with session_factory() as session, session.begin():
        session.add(
            QualificationCheck(
                participant_id=participant_id,
                phase="draw",
                state=QualificationState.ELIGIBLE,
                observed_status="not_required",
                observed_at=now,
            )
        )
    snapshot = await settlement.settle(campaign_id, now)
    drawing = DrawingService(uow_factory)
    first = await drawing.run(campaign_id, actor_user_id=101)
    second = await drawing.run(campaign_id, actor_user_id=101)
    deliveries = await QueryService(session_factory).delivery_summary(campaign_id)
    assert deliveries.pending == 2
    assert deliveries.confirmed == 0
    await _assert_draw_result(
        session_factory,
        snapshot=snapshot,
        first_id=first.id,
        second_id=second.id,
    )


async def _seed_settlement_case(
    session_factory, *, now: datetime, cutoff: datetime
) -> tuple[UUID, UUID]:
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session, state=CampaignState.SETTLING, cutoff_at=cutoff
        )
        session.add(
            PollingOffset(
                bot_id=123,
                last_durable_update_id=50,
                last_poll_succeeded_at=now,
                updated_at=now,
            )
        )
        session.add(
            TicketLedger(
                campaign_id=campaign.id,
                participant_id=participant.id,
                delta=3,
                source_type=TicketSource.POPULARITY,
                source_ref="seed",
                business_key="seed-ticket",
                occurred_at=cutoff - timedelta(minutes=1),
            )
        )
        return campaign.id, participant.id


async def _assert_draw_result(
    session_factory,
    *,
    snapshot: DrawSnapshot,
    first_id: UUID,
    second_id: UUID,
) -> None:
    async with session_factory() as session:
        snapshot_count = await session.scalar(select(func.count(DrawSnapshot.id)))
        entry_count = await session.scalar(select(func.count(DrawEntry.id)))
        winner_count = await session.scalar(select(func.count(Winner.id)))
        outbox_count = await session.scalar(select(func.count(TelegramOutbox.id)))
        outboxes = (await session.scalars(select(TelegramOutbox))).all()
        assert snapshot.total_weight == 3
        assert snapshot_count == 1
        assert entry_count == 1
        assert winner_count == 1
        assert outbox_count == 2
        assert all(row.aggregate_id != "None" for row in outboxes)
        assert all("None" not in row.business_key for row in outboxes)
        assert first_id == second_id
