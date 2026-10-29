from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, User

from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_callbacks import (
    REGISTRATION_REMINDER,
    bonus_callback,
    group_invite_callback,
    invite_link_callback,
    invite_page_callback,
)
from tg_lottery.telegram.member_flow import MemberFlow

PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"
GROUP_CHAT_ID = -100888
TARGET_CHAT_ID = -100999


class ReminderParticipants:
    def __init__(self, error="仅合格参与者可获取本活动群邀请链接"):
        self.reminders = []
        self.error = error

    async def eligible_participant(self, campaign_id, user_id):
        return None

    async def queue_temporary_reminder(self, **values):
        self.reminders.append(values)

    async def request_group_invite_link(self, *args, **kwargs):
        raise UserFacingError(self.error)


class Queries:
    def __init__(self, campaign):
        self.campaign = campaign

    async def campaign_by_reference(self, reference):
        return self.campaign

    async def invite_link_for_participant(self, campaign_id, user_id):
        return None

    async def participant_campaign_detail(self, user_id, reference):
        return None


def _campaign():
    return SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        state=CampaignState.RUNNING,
        rules=CampaignRules(bonus_types=("invite",)).model_dump(mode="json"),
    )


def _query():
    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP_CHAT_ID, type="supergroup"),
        from_user=User(id=123, is_bot=True, first_name="Bot"),
        text="campaign card",
    )
    return SimpleNamespace(
        id="temporary-reminder-callback",
        message=message,
        from_user=User(id=201, is_bot=False, first_name="Human"),
        answers=[],
    )


def _attach_answer(query):
    async def answer(text=None, show_alert=False):
        query.answers.append((text, show_alert))

    query.answer = answer
    return query


def _flow(campaign, participants):
    return MemberFlow(
        bot=object(),
        participants=participants,
        queries=Queries(campaign),
        cipher=object(),
        bot_username="tgccjbot",
    )


async def test_bonus_without_participation_queues_group_reminder():
    participants = ReminderParticipants()
    query = _attach_answer(_query())
    await bonus_callback(_flow(_campaign(), participants), query, PUBLIC_REF)

    assert query.answers == [(REGISTRATION_REMINDER, True)]
    assert participants.reminders[0]["text"] == REGISTRATION_REMINDER
    assert "立即参与" not in participants.reminders[0]["text"]
    assert participants.reminders[0]["recipient_chat_id"] == GROUP_CHAT_ID
    assert participants.reminders[0]["business_key"].startswith("temporary_reminder:")
    assert participants.reminders[0]["campaign_ref"] == PUBLIC_REF


async def test_group_invite_without_participation_queues_group_reminder():
    participants = ReminderParticipants()
    campaign = _campaign()
    query = _attach_answer(_query())
    await group_invite_callback(
        _flow(campaign, participants), query, f"{PUBLIC_REF}:{TARGET_CHAT_ID}"
    )

    reason = "仅合格参与者可获取本活动群邀请链接"
    assert query.answers == [(reason, True)]
    assert participants.reminders[0]["text"] == reason


@pytest.mark.parametrize(
    "text",
    ["活动当前不接受新增邀请入口", "请先点击别的入口"],
)
async def test_unrelated_group_error_is_not_queued_as_reminder(text):
    participants = ReminderParticipants(text)
    query = _attach_answer(_query())

    with pytest.raises(UserFacingError, match=text):
        await group_invite_callback(
            _flow(_campaign(), participants), query, f"{PUBLIC_REF}:{TARGET_CHAT_ID}"
        )

    assert participants.reminders == []
    assert query.answers == []


async def test_no_bonus_rules_queues_group_reminder_with_join_link():
    participants = ReminderParticipants()
    campaign = _campaign()
    campaign.rules = CampaignRules().model_dump(mode="json")
    query = _attach_answer(_query())
    await bonus_callback(_flow(campaign, participants), query, PUBLIC_REF)
    assert participants.reminders[0]["text"] == "本期抽奖没有开启可用的邀请或发言抽奖加成。"
    assert participants.reminders[0]["campaign_ref"] == PUBLIC_REF


@pytest.mark.parametrize("page", [None, 1])
async def test_invite_callbacks_deliver_only_to_clicker_private_chat(page):
    flow = _flow(_campaign(), ReminderParticipants())
    calls = []

    async def show(message, reference, *, user_id, page=0):
        calls.append((message.chat.id, message.chat.type, user_id, page))

    flow.show_invite_link = show
    query = _attach_answer(_query())
    if page is None:
        await invite_link_callback(flow, query, PUBLIC_REF)
    else:
        await invite_page_callback(flow, query, f"{PUBLIC_REF}:{page}")
    assert calls == [(201, "private", 201, page or 0)]
    assert query.answers == [("邀请入口已私聊发送给你", False)]


async def test_invite_private_delivery_failure_never_falls_back_to_group():
    participants = ReminderParticipants()
    flow = _flow(_campaign(), participants)
    targets = []

    async def show(message, reference, *, user_id, page=0):
        targets.append(message.chat.id)
        raise TelegramForbiddenError(
            method=SendMessage(chat_id=user_id, text="link"), message="blocked"
        )

    flow.show_invite_link = show
    query = _attach_answer(_query())
    await invite_link_callback(flow, query, PUBLIC_REF)
    assert targets == [201]
    assert participants.reminders == []
    assert query.answers == [("请先私聊机器人，再查看邀请入口", True)]  # noqa: RUF001


@pytest.mark.parametrize("rules", [CampaignRules(), CampaignRules(bonus_types=("invite",))])
async def test_group_invite_entry_precondition_queues_temporary_reminder(rules):
    participants = ReminderParticipants()
    campaign = _campaign()
    campaign.rules = rules.model_dump(mode="json")
    query = _attach_answer(_query())
    await invite_link_callback(_flow(campaign, participants), query, PUBLIC_REF)
    assert participants.reminders[0]["recipient_chat_id"] == GROUP_CHAT_ID
    assert participants.reminders[0]["campaign_ref"] == PUBLIC_REF
