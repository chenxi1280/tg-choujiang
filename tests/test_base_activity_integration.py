from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import func, select

from tests.test_v22_group_bonus_integration import _seed_activity_campaign
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.participants import ParticipantService, RegistrationRequest
from tg_lottery.application.queries import QueryService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import TicketSource
from tg_lottery.domain.rules import BaseActivityRule, CampaignRules, GroupActivityRule
from tg_lottery.infrastructure.db import (
    BaseActivityEvent,
    Campaign,
    Participant,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork

START = datetime(2026, 9, 1, 2, tzinfo=UTC)


async def seed(session_factory, *, days=7, messages=5, second=False):
    base = {"-100123": BaseActivityRule(consecutive_days=days, daily_messages=messages)}
    if second:
        base["-100124"] = BaseActivityRule(consecutive_days=days, daily_messages=messages)
    rules = CampaignRules(
        base_activity_rules=base,
        bonus_types=("activity",),
        group_activity_rules={"-100123": GroupActivityRule(messages_per_cycle=messages)},
    )
    campaign_id, participant_id = await _seed_activity_campaign(session_factory, rules, START)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.preheat_at = START - timedelta(days=1)
        campaign.cutoff_at = START + timedelta(days=20)
        campaign.draw_at = campaign.cutoff_at + timedelta(minutes=10)
        participant = await session.get(Participant, participant_id)
        participant.base_qualified = False
        participant.ticket_total_cache = 1
        session.add(
            TicketLedger(
                campaign_id=campaign_id,
                participant_id=participant_id,
                delta=1,
                source_type=TicketSource.REGISTRATION,
                source_ref=str(participant_id),
                business_key=f"registration:{participant_id}",
                occurred_at=START,
            )
        )
    return campaign_id, participant_id


async def messages(service, *, day, count=5, chat_id=-100123):
    for number in range(count):
        observation = MessageObservation(
            chat_id,
            day * 100 + number + 1,
            201,
            START + timedelta(days=day, minutes=number),
            f"这是有效发言第{day}天第{number}条",
        )
        await service.record_message(observation)
        await service.record_message(observation)


async def test_seven_days_stashes_bonus_then_enables_same_ledger_and_deduplicates(session_factory):
    campaign_id, participant_id = await seed(session_factory)
    uow = partial(UnitOfWork, session_factory)
    service = ActivityService(uow)
    settlement = SettlementService(uow)
    for day in range(6):
        await messages(service, day=day)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        assert not participant.base_qualified
        assert participant.ticket_total_cache == 7
        assert not await settlement._eligible_entries(session, campaign_id)
        notices = list(await session.scalars(select(TelegramOutbox)))
        assert len(notices) == 12
        assert all("暂存" in notice.payload["ticket_status"] for notice in notices)
    await messages(service, day=6)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        assert participant.base_qualified
        entries = await settlement._eligible_entries(session, campaign_id)
        assert [(item.id, weight) for item, weight in entries] == [(participant_id, 8)]
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 35
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 15
    detail = await QueryService(session_factory).participant_campaign_detail(
        201, await ref(session_factory, campaign_id)
    )
    assert detail.base_qualified
    assert "已达标" in detail.base_progress


async def test_each_group_must_qualify_and_gate_only_group_does_not_award_legacy_bonus(
    session_factory,
):
    _, participant_id = await seed(session_factory, days=1, messages=1, second=True)
    service = ActivityService(partial(UnitOfWork, session_factory))
    await messages(service, day=0, count=1)
    async with session_factory() as session:
        assert not (await session.get(Participant, participant_id)).base_qualified
    await messages(service, day=0, count=10, chat_id=-100124)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        assert participant.base_qualified
        assert participant.ticket_total_cache == 2
        notices = list(await session.scalars(select(TelegramOutbox)))
        assert len(notices) == 4
        assert {item.recipient_chat_id for item in notices} == {-100123, -100124}


async def test_midnight_boundary_and_rejections_are_independent_of_bonus_minimum(session_factory):
    campaign_id, participant_id = await seed(session_factory, days=2, messages=1)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.rules = campaign.rules | {
            "group_activity_rules": {
                "-100123": GroupActivityRule(min_text_length=20).model_dump(mode="json")
            }
        }
    service = ActivityService(partial(UnitOfWork, session_factory))
    before = datetime(2026, 9, 1, 15, 59, tzinfo=UTC)
    after = before + timedelta(minutes=2)
    await service.record_message(MessageObservation(-100123, 1, 201, before, "这是午夜之前的发言"))
    await service.record_message(MessageObservation(-100123, 2, 201, after, "这是午夜之后的发言"))
    await service.record_message(MessageObservation(-100123, 3, 201, after, "这是午夜之后的发言"))
    await service.record_message(MessageObservation(-100123, 4, 201, after, "/这是命令不计数"))
    async with session_factory() as session:
        assert (await session.get(Participant, participant_id)).base_qualified
        rows = list(
            await session.scalars(select(BaseActivityEvent).order_by(BaseActivityEvent.message_id))
        )
        assert [row.accepted for row in rows] == [True, True, False, False]
        assert (await session.get(Participant, participant_id)).ticket_total_cache == 1
        assert rows[0].local_date.isoformat() == "2026-09-01"
        assert rows[1].local_date.isoformat() == "2026-09-02"


async def test_new_registration_starts_gate_pending(session_factory):
    campaign_id, _ = await seed(session_factory)
    service = ParticipantService(partial(UnitOfWork, session_factory))
    attempt = await service.begin_registration(
        RegistrationRequest(campaign_id, 202, None, "新用户", START)
    )
    async with session_factory() as session:
        assert not (await session.get(Participant, attempt.participant_id)).base_qualified


async def ref(session_factory, campaign_id):
    async with session_factory() as session:
        return (await session.get(Campaign, campaign_id)).public_ref
