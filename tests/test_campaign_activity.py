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

DAILY_ACTIVITY_TEST_HOUR = 12


def _daily_activity_base() -> datetime:
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    local_midday = local_now.replace(
        hour=DAILY_ACTIVITY_TEST_HOUR, minute=0, second=0, microsecond=0
    )
    return local_midday.astimezone(UTC)


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
        assert outbox.payload["cutoff_at"] == stored.cutoff_at.isoformat()
        assert outbox.payload["draw_at"] == stored.draw_at.isoformat()
        assert outbox.payload["timezone"] == stored.timezone
        assert outbox.payload["campaign_ref"] == stored.public_ref
        assert stored.public_ref != str(stored.id)


async def test_activity_awards_one_daily_ticket_and_rejects_duplicates(
    session_factory,
) -> None:
    rules = CampaignRules(luck_mode="standard")
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session, rules=rules)
        campaign_id = campaign.id
        participant_id = participant.id
        chat_id = group.chat_id
    service = ActivityService(partial(UnitOfWork, session_factory))
    base = _daily_activity_base()
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


async def test_luck_off_records_activity_without_awarding_tickets(session_factory) -> None:
    rules = CampaignRules(
        luck_mode="off",
        activity=ActivityRule(message_threshold=1, daily_ticket_cap=1),
    )
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session, rules=rules)
        campaign_id = campaign.id
        participant_id = participant.id
    service = ActivityService(partial(UnitOfWork, session_factory))

    event = await service.record_message(
        MessageObservation(
            chat_id=group.chat_id,
            message_id=1,
            user_id=201,
            event_at=datetime.now(UTC),
            text="关闭加成仍记录有效活跃",
        )
    )

    async with session_factory() as session:
        ticket_count = await session.scalar(
            select(func.count(TicketLedger.id)).where(TicketLedger.campaign_id == campaign_id)
        )
        stored = await session.get(type(participant), participant_id)
    assert event is not None and event.accepted is True
    assert ticket_count == 0
    assert stored.ticket_total_cache == 0


async def test_activity_bonus_is_settled_once_for_each_local_day(session_factory) -> None:
    rules = CampaignRules(
        bonus_types=("activity",),
        activity=ActivityRule(
            message_threshold=2,
            reward_tickets=3,
            daily_ticket_cap=3,
            settle_once_per_day=True,
        ),
    )
    base = _daily_activity_base()
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(
            session, rules=rules, cutoff_at=base + timedelta(days=3)
        )
        campaign_id = campaign.id
        chat_id = group.chat_id
    service = ActivityService(partial(UnitOfWork, session_factory))

    for index, event_at in enumerate(
        (
            base,
            base + timedelta(minutes=1),
            base + timedelta(minutes=2),
            base + timedelta(days=1),
            base + timedelta(days=1, minutes=1),
        ),
        start=1,
    ):
        await service.record_message(
            MessageObservation(
                chat_id=chat_id,
                message_id=700 + index,
                user_id=201,
                event_at=event_at,
                text=f"每日独立结算消息 {index}",
            )
        )

    async with session_factory() as session:
        ticket_total = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(TicketLedger.campaign_id == campaign_id)
        )
        ledger_count = await session.scalar(
            select(func.count(TicketLedger.id)).where(TicketLedger.campaign_id == campaign_id)
        )
        progress = (await session.scalars(select(DailyProgress))).all()
    assert ticket_total == 6
    assert ledger_count == 2
    assert [item.tickets_awarded for item in progress] == [3, 3]


async def test_activity_bonus_requires_each_required_group_to_reach_threshold(
    session_factory,
) -> None:
    extra_chat_id = -100456
    rules = CampaignRules(
        required_chat_ids=(extra_chat_id,),
        bonus_types=("activity",),
        activity=ActivityRule(
            message_threshold=2,
            reward_tickets=3,
            daily_ticket_cap=3,
            settle_once_per_day=True,
        ),
    )
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session, rules=rules)
        campaign.presentation = {
            "main_chat": {"id": group.chat_id, "type": "supergroup", "title": "主群"},
            "required_chats": [{"id": extra_chat_id, "type": "supergroup", "title": "协作群"}],
        }
        campaign_id = campaign.id
        main_chat_id = group.chat_id
    service = ActivityService(partial(UnitOfWork, session_factory))
    base = _daily_activity_base()

    await _record_group_messages(service, main_chat_id, base, start_id=800)
    async with session_factory() as session:
        assert await session.scalar(select(func.count(TicketLedger.id))) == 0
    await _record_group_messages(service, extra_chat_id, base, start_id=900)

    async with session_factory() as session:
        progress = await session.scalar(select(DailyProgress))
        total = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(TicketLedger.campaign_id == campaign_id)
        )
    assert progress.valid_message_counts == {str(main_chat_id): 2, str(extra_chat_id): 2}
    assert total == 3


async def _record_group_messages(
    service: ActivityService,
    chat_id: int,
    base: datetime,
    *,
    start_id: int,
) -> None:
    for offset in range(2):
        await service.record_message(
            MessageObservation(
                chat_id=chat_id,
                message_id=start_id + offset,
                user_id=201,
                event_at=base + timedelta(minutes=offset),
                text=f"群 {chat_id} 的有效消息 {offset}",
            )
        )


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
        luck_mode="standard",
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
    rules = CampaignRules(
        luck_mode="standard",
        activity=ActivityRule(message_threshold=1, daily_ticket_cap=1),
    )
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


async def test_cancelling_published_campaign_queues_card_compensation(
    session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign.published_message_id = 321
        campaign_id = campaign.id
    service = CampaignService(partial(UnitOfWork, session_factory))
    await service.change_state(
        campaign_id,
        actor_user_id=101,
        target=CampaignState.CANCELLED,
        reason="测试取消",
    )
    async with session_factory() as session:
        outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "edit_campaign_card_cancelled")
        )
        revoke_job = await session.scalar(
            select(ScheduledJob).where(ScheduledJob.business_key.startswith("revoke_links_cancel:"))
        )
        assert outbox.payload == {
            "campaign_name": "测试活动",
            "has_cover": False,
            "message_id": 321,
        }
        assert revoke_job.payload == {"revoke_personal": True}


async def test_repeated_early_cutoff_does_not_duplicate_card_update(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign.published_message_id = 654
        campaign_id = campaign.id
    service = CampaignService(partial(UnitOfWork, session_factory))
    cutoff_at = datetime.now(UTC)
    for _ in range(2):
        await service.early_cutoff(
            campaign_id,
            actor_user_id=101,
            cutoff_at=cutoff_at,
            reason="测试提前截止",
        )
    async with session_factory() as session:
        outbox_count = await session.scalar(
            select(func.count(TelegramOutbox.id)).where(
                TelegramOutbox.operation == "edit_campaign_card_state"
            )
        )
        assert outbox_count == 1
