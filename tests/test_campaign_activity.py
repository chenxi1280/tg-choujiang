from datetime import UTC, datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.campaigns import CampaignService, PrizeSpec
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import ActivityRule, CampaignDraft, CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    DailyProgress,
    Group,
    GroupAdmin,
    ScheduledJob,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_create_and_publish_freezes_rules_and_schedules_full_lifecycle(
    session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        group = Group(chat_id=-100500, title="运营群", timezone="Asia/Shanghai")
        session.add(group)
        await session.flush()
        session.add(GroupAdmin(group_id=group.id, user_id=101, source="owner", active=True))
    now = datetime.now(UTC)
    service = CampaignService(partial(UnitOfWork, session_factory))
    campaign = await service.create_draft(
        CampaignDraft(
            group_id=-100500,
            name="发布测试",
            preheat_at=now + timedelta(minutes=5),
            cutoff_at=now + timedelta(days=1),
            draw_at=now + timedelta(days=1, minutes=10),
        ),
        (PrizeSpec("一等奖", 1, "联系管理员"),),
        101,
    )
    await service.publish(campaign.id, 101, "lottery_test_bot")
    await service.publish(campaign.id, 101, "lottery_test_bot")
    async with session_factory() as session:
        stored = await session.get(Campaign, campaign.id)
        job_count = await session.scalar(
            select(func.count(ScheduledJob.id)).where(ScheduledJob.aggregate_id == str(campaign.id))
        )
        outbox_count = await session.scalar(
            select(func.count(TelegramOutbox.id)).where(
                TelegramOutbox.aggregate_id == str(campaign.id)
            )
        )
        assert stored.state is CampaignState.PUBLISHING
        assert stored.prize_plan_hash is not None
        assert job_count == 6
        assert outbox_count == 1
        outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.aggregate_id == str(campaign.id))
        )
        assert outbox.payload["total_winners"] == 1
        assert outbox.payload["prizes"][0]["winner_count"] == 1


async def test_activity_awards_one_daily_ticket_and_rejects_duplicates(
    session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session)
        campaign_id = campaign.id
        participant_id = participant.id
        chat_id = group.chat_id
    service = ActivityService(partial(UnitOfWork, session_factory))
    base = datetime.now(UTC)
    for index in range(10):
        await service.record_message(
            MessageObservation(
                chat_id=chat_id,
                message_id=index + 1,
                user_id=201,
                event_at=base + timedelta(minutes=index),
                text=f"有效消息内容 {index}",
            )
        )
    duplicate = await service.record_message(
        MessageObservation(
            chat_id=chat_id,
            message_id=99,
            user_id=201,
            event_at=base + timedelta(minutes=9, seconds=30),
            text="有效消息内容 9",
        )
    )
    replay = await service.record_message(
        MessageObservation(
            chat_id=chat_id,
            message_id=1,
            user_id=201,
            event_at=base,
            text="有效消息内容 0",
        )
    )
    async with session_factory() as session:
        tickets = await session.scalar(
            select(func.coalesce(func.sum(TicketLedger.delta), 0)).where(
                TicketLedger.campaign_id == campaign_id
            )
        )
        stored = await session.get(type(participant), participant_id)
        assert tickets == 1
        assert stored.ticket_total_cache == 1
        assert duplicate is not None and duplicate.accepted is False
        assert replay is not None and replay.message_id == 1


async def test_activity_uses_campaign_timezone_and_inclusive_cutoff(session_factory) -> None:
    zone = ZoneInfo("Asia/Kuala_Lumpur")
    local_now = datetime.now(zone)
    next_midnight = (local_now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    before_midnight = next_midnight - timedelta(minutes=1)
    after_midnight = next_midnight + timedelta(minutes=1)
    rules = CampaignRules(
        timezone=zone.key,
        activity=ActivityRule(message_threshold=1, daily_ticket_cap=1),
    )
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session, rules=rules, cutoff_at=after_midnight)
        campaign_id = campaign.id
        chat_id = group.chat_id
    service = ActivityService(partial(UnitOfWork, session_factory))
    for message_id, event_at in ((1, before_midnight), (2, after_midnight)):
        event = await service.record_message(
            MessageObservation(
                chat_id=chat_id,
                message_id=message_id,
                user_id=201,
                event_at=event_at,
                text=f"跨日有效消息 {message_id}",
            )
        )
        assert event is not None and event.accepted is True
    rejected = await service.record_message(
        MessageObservation(
            chat_id=chat_id,
            message_id=3,
            user_id=201,
            event_at=after_midnight + timedelta(seconds=1),
            text="截止后的消息",
        )
    )
    async with session_factory() as session:
        ticket_total = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(TicketLedger.campaign_id == campaign_id)
        )
        day_count = await session.scalar(select(func.count(DailyProgress.id)))
        assert rejected is None
        assert ticket_total == 2
        assert day_count == 2


async def test_activity_before_preheat_is_not_replayed_into_running_campaign(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session)
        campaign.preheat_at = now
        chat_id = group.chat_id
    event = await ActivityService(partial(UnitOfWork, session_factory)).record_message(
        MessageObservation(
            chat_id=chat_id,
            message_id=500,
            user_id=201,
            event_at=now - timedelta(seconds=1),
            text="预热前的延迟消息",
        )
    )
    async with session_factory() as session:
        assert event is None
        assert await session.scalar(select(func.count(TicketLedger.id))) == 0


async def test_pre_cutoff_message_is_counted_while_inbox_drains(session_factory) -> None:
    rules = CampaignRules(activity=ActivityRule(message_threshold=1, daily_ticket_cap=1))
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        group, _, _ = await seed_campaign(
            session,
            state=CampaignState.SETTLING,
            rules=rules,
            cutoff_at=now,
        )
        chat_id = group.chat_id
    event = await ActivityService(partial(UnitOfWork, session_factory)).record_message(
        MessageObservation(
            chat_id=chat_id,
            message_id=501,
            user_id=201,
            event_at=now,
            text="截止前已接收的排空消息",
        )
    )
    async with session_factory() as session:
        assert event is not None and event.accepted is True
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 1


async def test_stale_draft_cannot_be_published(session_factory) -> None:
    async with session_factory() as session, session.begin():
        group = Group(chat_id=-100501, title="过期发布群", timezone="Asia/Shanghai")
        session.add(group)
        await session.flush()
        session.add(GroupAdmin(group_id=group.id, user_id=101, source="owner", active=True))
    now = datetime.now(UTC)
    service = CampaignService(partial(UnitOfWork, session_factory))
    campaign = await service.create_draft(
        CampaignDraft(
            group_id=-100501,
            name="过期活动",
            preheat_at=now + timedelta(minutes=1),
            cutoff_at=now + timedelta(minutes=2),
            draw_at=now + timedelta(minutes=3),
        ),
        (PrizeSpec("一等奖", 1, "联系管理员"),),
        101,
    )
    async with session_factory() as session, session.begin():
        stored = await session.get(Campaign, campaign.id)
        stored.cutoff_at = now - timedelta(seconds=1)
        stored.draw_at = now + timedelta(minutes=1)
    with pytest.raises(ValueError, match="cutoff must be in the future"):
        await service.publish(campaign.id, 101, "lottery_test_bot")
