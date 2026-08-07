from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    QualificationState,
    TicketSource,
)
from tg_lottery.domain.errors import InvitationValidationDeferred, SettlementBlocked
from tg_lottery.infrastructure.db import (
    DrawEntry,
    DrawSnapshot,
    InviteLink,
    PollingOffset,
    QualificationCheck,
    TelegramOutbox,
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
    validated = await service.validate(
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
        assert snapshot.total_weight == 3
        assert snapshot_count == 1
        assert entry_count == 1
        assert winner_count == 1
        assert outbox_count == 2
        assert first_id == second_id
