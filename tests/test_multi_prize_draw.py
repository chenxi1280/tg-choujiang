from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState, QualificationState, TicketSource
from tg_lottery.domain.errors import SettlementBlocked
from tg_lottery.infrastructure.db import (
    DrawRound,
    Participant,
    PollingOffset,
    Prize,
    QualificationCheck,
    TelegramOutbox,
    TicketLedger,
    Winner,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_multiple_prizes_draw_unique_winners_and_report_unfilled_slots(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    campaign_id = await _seed_multi_prize_case(session_factory, now)
    uow_factory = partial(UnitOfWork, session_factory)
    await SettlementService(uow_factory).settle(campaign_id, now)
    await DrawingService(uow_factory).run(campaign_id, actor_user_id=101)

    async with session_factory() as session:
        winners = (await session.scalars(select(Winner))).all()
        rounds = (await session.scalars(select(DrawRound).order_by(DrawRound.round_number))).all()
        outboxes = (await session.scalars(select(TelegramOutbox))).all()

    assert len(winners) == 3
    assert len({winner.participant_id for winner in winners}) == 3
    assert [(row.prize_slot_no, row.undistributed) for row in rounds] == [
        (1, False),
        (2, False),
        (1, False),
        (2, True),
    ]
    group_payload = next(row.payload for row in outboxes if row.operation == "send_draw_result")
    assert group_payload["undistributed"] == [
        {"prize_position": 2, "prize_name": "二等奖", "count": 1}
    ]
    notices = [row.payload for row in outboxes if row.operation == "send_winner_notice"]
    assert group_payload["extended_tips"] is True
    assert all(notice["extended_tips"] is True for notice in notices)
    assert all(item["claim_instructions"] for item in notices)
    assert {item["prize_slot_no"] for item in notices} <= {1, 2}


async def test_settlement_blocks_when_published_prize_plan_changes(session_factory) -> None:
    now = datetime.now(UTC)
    campaign_id = await _seed_multi_prize_case(session_factory, now)
    async with session_factory() as session, session.begin():
        prize = await session.scalar(
            select(Prize).where(Prize.campaign_id == campaign_id).order_by(Prize.position)
        )
        assert prize is not None
        prize.winner_count += 1

    service = SettlementService(partial(UnitOfWork, session_factory))
    with pytest.raises(SettlementBlocked, match="prize_plan_changed_after_publish"):
        await service.settle(campaign_id, now)


async def _seed_multi_prize_case(session_factory, now: datetime):
    cutoff = now - timedelta(minutes=10)
    async with session_factory() as session, session.begin():
        _, campaign, first = await seed_campaign(
            session, state=CampaignState.SETTLING, cutoff_at=cutoff
        )
        prizes = (await session.scalars(select(Prize))).all()
        prizes[0].winner_count = 2
        prizes[0].claim_instructions = "私聊管理员领取一等奖"
        prizes.append(
            Prize(
                campaign_id=campaign.id,
                position=2,
                name="二等奖",
                winner_count=2,
                claim_instructions="私聊管理员领取二等奖",
            )
        )
        session.add(prizes[-1])
        participants = [first, *_extra_participants(campaign.id)]
        session.add_all(participants[1:])
        await session.flush()
        campaign.prize_plan_hash = prize_plan_hash(prizes)
        session.add(_polling_offset(now))
        for index, participant in enumerate(participants, start=1):
            session.add(_draw_check(participant, now))
            session.add(_ticket(campaign.id, participant, cutoff=cutoff, index=index))
        return campaign.id


def _extra_participants(campaign_id):
    return [
        Participant(
            campaign_id=campaign_id,
            user_id=user_id,
            display_name=f"成员{index}",
            private_reachable=True,
            qualification_state=QualificationState.ELIGIBLE,
            registered_revision=1,
        )
        for index, user_id in enumerate((202, 203), start=2)
    ]


def _polling_offset(now: datetime) -> PollingOffset:
    return PollingOffset(
        bot_id=123,
        last_durable_update_id=50,
        last_poll_succeeded_at=now,
        updated_at=now,
    )


def _draw_check(participant: Participant, now: datetime) -> QualificationCheck:
    return QualificationCheck(
        participant_id=participant.id,
        phase="draw",
        state=QualificationState.ELIGIBLE,
        observed_status="not_required",
        observed_at=now,
    )


def _ticket(campaign_id, participant: Participant, *, cutoff: datetime, index: int) -> TicketLedger:
    return TicketLedger(
        campaign_id=campaign_id,
        participant_id=participant.id,
        delta=index,
        source_type=TicketSource.POPULARITY,
        source_ref=f"multi-{index}",
        business_key=f"multi-ticket-{index}",
        occurred_at=cutoff - timedelta(minutes=1),
    )
