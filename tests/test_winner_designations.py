from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.application.settlement import SettlementService
from tg_lottery.application.winner_designations import WinnerDesignationService
from tg_lottery.domain.enums import CampaignState, QualificationState, TicketSource
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import (
    AuditLog,
    DrawRound,
    Participant,
    PollingOffset,
    Prize,
    QualificationCheck,
    TelegramOutbox,
    TicketLedger,
    Winner,
    WinnerDesignation,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_designated_slot_uses_selected_participant_and_records_audit(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    campaign_id, _, chosen_id = await _seed_case(session_factory, now, winner_count=1)
    service = WinnerDesignationService(partial(UnitOfWork, session_factory))

    row = await service.set(
        campaign_id=campaign_id,
        prize_position=1,
        prize_slot_no=1,
        participant_reference="@chosen",
        actor_user_id=101,
    )
    await _settle_and_draw(session_factory, campaign_id, now)

    async with session_factory() as session:
        winner = await session.scalar(select(Winner))
        draw_round = await session.scalar(select(DrawRound))
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "winner.designated"))
        group_result = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_draw_result")
        )
    assert row.participant_username == "chosen"
    assert winner is not None and winner.participant_id == chosen_id
    assert draw_round is not None and draw_round.selection_mode == "designated"
    assert draw_round.designation_id is not None
    assert audit is not None and audit.actor_user_id == 101
    assert group_result is not None and group_result.payload["extended_tips"] is False
    assert "selection_mode" not in group_result.payload
    assert "designation_id" not in group_result.payload


async def test_random_slots_reserve_participants_designated_for_later_slots(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    campaign_id, first_id, chosen_id = await _seed_case(session_factory, now, winner_count=2)
    service = WinnerDesignationService(partial(UnitOfWork, session_factory))
    await service.set(
        campaign_id=campaign_id,
        prize_position=1,
        prize_slot_no=2,
        participant_reference=str(202),
        actor_user_id=101,
    )

    await _settle_and_draw(session_factory, campaign_id, now)

    async with session_factory() as session:
        rounds = tuple(await session.scalars(select(DrawRound).order_by(DrawRound.round_number)))
    assert [row.selected_participant_id for row in rounds] == [first_id, chosen_id]
    assert [row.selection_mode for row in rounds] == ["random", "designated"]


async def test_ineligible_designated_participant_blocks_draw_without_silent_replacement(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    campaign_id, _, chosen_id = await _seed_case(
        session_factory,
        now,
        winner_count=1,
        chosen_state=QualificationState.INELIGIBLE,
    )
    service = WinnerDesignationService(partial(UnitOfWork, session_factory))
    await service.set(
        campaign_id=campaign_id,
        prize_position=1,
        prize_slot_no=1,
        participant_reference="@chosen",
        actor_user_id=101,
    )
    uow_factory = partial(UnitOfWork, session_factory)
    await SettlementService(uow_factory).settle(campaign_id, now)

    with pytest.raises(UserFacingError, match="未通过最终资格复核"):
        await DrawingService(uow_factory).run(campaign_id, actor_user_id=101)

    async with session_factory() as session:
        assert await session.scalar(select(Winner)) is None
        designation = await session.scalar(select(WinnerDesignation))
        assert designation is not None and designation.participant_id == chosen_id


async def test_designation_can_be_removed_before_draw(session_factory) -> None:
    now = datetime.now(UTC)
    campaign_id, _, _ = await _seed_case(session_factory, now, winner_count=1)
    service = WinnerDesignationService(partial(UnitOfWork, session_factory))
    await service.set(
        campaign_id=campaign_id,
        prize_position=1,
        prize_slot_no=1,
        participant_reference="@chosen",
        actor_user_id=101,
    )

    removed = await service.remove(
        campaign_id=campaign_id,
        prize_position=1,
        prize_slot_no=1,
        actor_user_id=101,
    )

    assert removed is True
    assert await service.list(campaign_id) == ()
    async with session_factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "winner.designation_removed")
        )
        assert audit is not None and audit.actor_user_id == 101


async def _settle_and_draw(session_factory, campaign_id, now: datetime) -> None:
    uow_factory = partial(UnitOfWork, session_factory)
    await SettlementService(uow_factory).settle(campaign_id, now)
    await DrawingService(uow_factory).run(campaign_id, actor_user_id=101)


async def _seed_case(
    session_factory,
    now: datetime,
    *,
    winner_count: int,
    chosen_state: QualificationState = QualificationState.ELIGIBLE,
):
    cutoff = now - timedelta(minutes=10)
    async with session_factory() as session, session.begin():
        _, campaign, first = await seed_campaign(
            session,
            state=CampaignState.SETTLING,
            cutoff_at=cutoff,
        )
        campaign.published_message_id = 321
        first.username = "first"
        chosen = Participant(
            campaign_id=campaign.id,
            user_id=202,
            username="chosen",
            display_name="指定成员",
            private_reachable=True,
            qualification_state=chosen_state,
            registered_revision=1,
        )
        session.add(chosen)
        prize = await session.scalar(select(Prize).where(Prize.campaign_id == campaign.id))
        assert prize is not None
        prize.winner_count = winner_count
        campaign.prize_plan_hash = prize_plan_hash((prize,))
        await session.flush()
        session.add(_polling(now))
        for index, participant in enumerate((first, chosen), start=1):
            state = QualificationState.ELIGIBLE if participant is first else chosen_state
            session.add(_draw_check(participant.id, now, state=state))
            session.add(_ticket(campaign.id, participant.id, cutoff, index=index))
        return campaign.id, first.id, chosen.id


def _polling(now: datetime) -> PollingOffset:
    return PollingOffset(
        bot_id=123,
        last_durable_update_id=50,
        last_poll_succeeded_at=now,
        updated_at=now,
    )


def _draw_check(participant_id, now: datetime, *, state: QualificationState) -> QualificationCheck:
    return QualificationCheck(
        participant_id=participant_id,
        phase="draw",
        state=state,
        observed_status="member" if state is QualificationState.ELIGIBLE else "left",
        observed_at=now,
    )


def _ticket(campaign_id, participant_id, cutoff: datetime, *, index: int) -> TicketLedger:
    return TicketLedger(
        campaign_id=campaign_id,
        participant_id=participant_id,
        delta=index,
        source_type=TicketSource.REGISTRATION,
        source_ref=f"designation-{index}",
        business_key=f"designation-ticket-{index}",
        occurred_at=cutoff,
    )
