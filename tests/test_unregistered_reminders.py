import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tests.test_notice_deletion import NoticeBot, _deletion_facts, _delivery_worker
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.invitation_models import JoinObservation
from tg_lottery.application.invitations import InvitationService
from tg_lottery.domain.admission import MessageAdmission
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules, InviteRule
from tg_lottery.infrastructure.db import (
    ActivityEvent,
    Campaign,
    DailyProgress,
    InviteeActivityEvent,
    InviteLink,
    Participant,
    ScheduledJob,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork

CHAT_ID = -100123
NEW_USER_ID = 301


def _message_rules():
    return CampaignRules(
        participation_rules_version="v3.0",
        admission=MessageAdmission(type="message", groups={str(CHAT_ID): {"daily_messages": 2}}),
    )


async def _seed(factory, *, rules=None, state=CampaignState.RUNNING):
    async with factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, rules=rules or _message_rules(), state=state)
        return campaign.id, campaign.public_ref


def _observation(**values):
    return MessageObservation(
        chat_id=CHAT_ID,
        message_id=900,
        user_id=NEW_USER_ID,
        event_at=datetime.now(UTC),
        text="未报名成员的有效发言",
        **values,
    )


def _service(factory):
    return ActivityService(partial(UnitOfWork, factory))


async def _reminders(factory):
    async with factory() as session:
        return tuple(await session.scalars(select(TelegramOutbox)))


async def test_unregistered_speech_is_temporary_idempotent_and_never_registers_or_counts(
    session_factory,
):
    campaign_id, reference = await _seed(session_factory)
    message = _observation()
    service = _service(session_factory)
    assert await service.record_message(message) is None
    assert await service.record_message(message) is None
    reminders = await _reminders(session_factory)
    assert len(reminders) == 1
    row = reminders[0]
    assert row.operation == "send_temporary_reminder"
    assert row.aggregate_id == str(campaign_id)
    assert row.recipient_chat_id == CHAT_ID
    assert row.payload["campaign_ref"] == reference
    assert row.payload["user_id"] == NEW_USER_ID
    assert row.payload["delete_after_seconds"] == 15
    assert "未计入本人抽奖进度" in row.payload["text"]
    async with session_factory() as session:
        assert await session.scalar(select(func.count(Participant.id))) == 1
        for model in (ActivityEvent, DailyProgress, TicketLedger, ScheduledJob):
            assert await session.scalar(select(func.count(model.id))) == 0


async def test_first_reminder_is_once_per_campaign_and_separates_users(session_factory):
    await _seed(session_factory)
    message = _observation()
    service = _service(session_factory)
    await service.record_message(message)
    await service.record_message(
        replace(message, message_id=901, event_at=message.event_at + timedelta(minutes=4))
    )
    assert len(await _reminders(session_factory)) == 1
    await service.record_message(replace(message, message_id=902, user_id=302))
    assert len(await _reminders(session_factory)) == 2
    await service.record_message(
        replace(message, message_id=903, event_at=message.event_at + timedelta(minutes=5))
    )
    assert len(await _reminders(session_factory)) == 2
    await service.record_message(message)
    assert len(await _reminders(session_factory)) == 2


async def test_old_reminder_and_next_day_speech_do_not_rearm_first_notice(session_factory):
    campaign_id, _ = await _seed(session_factory)
    service = _service(session_factory)
    message = _observation()
    await service.record_message(message)
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(TelegramOutbox))
        row.business_key = f"unregistered_speech:{campaign_id}:{CHAT_ID}:{NEW_USER_ID}:900"
        campaign = await session.get(Campaign, campaign_id)
        campaign.cutoff_at = message.event_at + timedelta(days=3)
    await service.record_message(
        replace(message, message_id=904, event_at=message.event_at + timedelta(days=1))
    )
    assert len(await _reminders(session_factory)) == 1


async def test_group_migration_does_not_rearm_first_notice(session_factory):
    from tg_lottery.application.groups import GroupService

    await _seed(session_factory)
    service = _service(session_factory)
    message = _observation()
    await service.record_message(message)
    upgraded = -100999
    await GroupService(partial(UnitOfWork, session_factory)).migrate_group(CHAT_ID, upgraded)
    await service.record_message(replace(message, chat_id=upgraded, message_id=901))
    assert len(await _reminders(session_factory)) == 1


async def test_concurrent_unregistered_messages_queue_only_one_reminder(session_factory):
    await _seed(session_factory)
    message = _observation()
    service = _service(session_factory)
    await asyncio.gather(
        *(
            service.record_message(replace(message, message_id=message.message_id + index))
            for index in range(3)
        )
    )
    assert len(await _reminders(session_factory)) == 1


async def test_first_reminder_is_independent_in_another_configured_group(session_factory):
    second_chat = -100456
    rules = CampaignRules(
        participation_rules_version="v3.0",
        admission=MessageAdmission(
            type="message",
            groups={
                str(CHAT_ID): {"daily_messages": 2},
                str(second_chat): {"daily_messages": 2},
            },
        ),
        required_chat_ids=(second_chat,),
    )
    campaign_id, _ = await _seed(session_factory, rules=rules)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.presentation = {"required_chats": [{"id": second_chat, "type": "supergroup"}]}
    message = _observation()
    service = _service(session_factory)
    await service.record_message(message)
    await service.record_message(replace(message, chat_id=second_chat))
    assert {row.recipient_chat_id for row in await _reminders(session_factory)} == {
        CHAT_ID,
        second_chat,
    }


@pytest.mark.parametrize(
    "values",
    [
        {"is_bot": True},
        {"is_service": True},
        {"is_forwarded": True},
        {"text": "/start"},
        {"text": "  "},
        {"chat_id": -199999},
        {"event_at": datetime.now(UTC) - timedelta(days=2)},
        {"event_at": datetime.now(UTC) + timedelta(days=2)},
    ],
)
async def test_invalid_or_outside_scope_messages_do_not_remind(session_factory, values):
    await _seed(session_factory)
    await _service(session_factory).record_message(replace(_observation(), **values))
    assert await _reminders(session_factory) == ()


@pytest.mark.parametrize(
    "rules",
    [
        CampaignRules(),
        CampaignRules(bonus_types=("invite",)),
        CampaignRules(
            participation_rules_version="v3.0",
            admission=MessageAdmission(type="message", groups={"-100999": {"daily_messages": 2}}),
        ),
    ],
)
async def test_no_message_task_in_this_group_does_not_remind(session_factory, rules):
    await _seed(session_factory, rules=rules)
    await _service(session_factory).record_message(_observation())
    assert await _reminders(session_factory) == ()


async def test_existing_ineligible_participant_is_not_told_to_register(session_factory):
    await _seed(session_factory, rules=CampaignRules(bonus_types=("activity",)))
    async with session_factory() as session, session.begin():
        participant = await session.scalar(select(Participant))
        participant.qualification_state = QualificationState.INELIGIBLE
    await _service(session_factory).record_message(replace(_observation(), user_id=201))
    assert await _reminders(session_factory) == ()


@pytest.mark.parametrize("state", [CampaignState.SCHEDULED, CampaignState.DRAWN])
async def test_inactive_campaign_does_not_remind(session_factory, state):
    await _seed(session_factory, state=state)
    await _service(session_factory).record_message(_observation())
    assert await _reminders(session_factory) == ()


async def test_late_message_after_cutoff_never_prompts_new_registration(session_factory):
    campaign_id, _ = await _seed(session_factory, state=CampaignState.SETTLING)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.cutoff_at = now - timedelta(minutes=1)
    message = replace(_observation(), event_at=now - timedelta(minutes=2))
    await _service(session_factory).record_message(message)
    assert await _reminders(session_factory) == ()


async def test_speech_reminder_delivers_mention_and_same_campaign_link_then_schedules_deletion(
    session_factory,
):
    _, reference = await _seed(session_factory)
    await _service(session_factory).record_message(_observation())
    row = (await _reminders(session_factory))[0]
    bot = NoticeBot()
    assert await _delivery_worker(session_factory, bot).process_once()
    await _deletion_facts(session_factory, row.id)
    target, text, options = bot.sent[0]
    assert target == CHAT_ID
    assert f"tg://user?id={NEW_USER_ID}" in text
    assert options["parse_mode"] == "HTML"
    button = options["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == f"m:detail:{reference}"
    assert button.url is None
    assert f"https://t.me/lottery_test_bot?start=join_{reference}" in text
    assert options["reply_markup"].inline_keyboard[0][1].url == "https://t.me/lottery_test_bot"


async def test_legacy_invitee_activity_is_recorded_even_when_reminding_to_register(session_factory):
    rules = CampaignRules(
        bonus_types=("activity", "invite"), invite=InviteRule(require_activity=True)
    )
    campaign_id, _ = await _seed(session_factory, rules=rules)
    async with session_factory() as session, session.begin():
        participant = await session.scalar(select(Participant))
        link = InviteLink(
            campaign_id=campaign_id,
            participant_id=participant.id,
            telegram_name="legacy_test",
            link_hash="a" * 64,
        )
        session.add(link)
        await session.flush()
        link_id = link.id
    message = _observation()
    invitations = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await invitations.record_joined(
        JoinObservation(link_id, NEW_USER_ID, message.event_at)
    )
    assert invitation is not None
    event = await _service(session_factory).record_message(message)
    assert isinstance(event, InviteeActivityEvent)
    assert event.accepted
    assert len(await _reminders(session_factory)) == 1
