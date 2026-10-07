from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from aiogram.types import Chat, Message, User

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_callbacks import (
    REGISTRATION_REMINDER,
    bonus_callback,
    group_invite_callback,
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


def _campaign():
    return SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
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
