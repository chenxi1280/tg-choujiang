from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.campaigns import CampaignService, PrizeSpec
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignDraft
from tg_lottery.infrastructure.db import (
    Campaign,
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
        assert job_count == 6
        assert outbox_count == 1


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
