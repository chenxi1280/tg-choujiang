"""报名回复路径: 幂等重放、UNKNOWN 弹窗与弹窗文案分组。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import GetChatMember
from aiogram.types import User

from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.member_popups import popup_failure

PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"
MAIN_CHAT_ID = -100888
THRESHOLD_CHAT_ID = -100999


class FakeBot:
    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="member")


class UnavailableBot:
    async def get_chat_member(self, chat_id, user_id):
        raise TelegramAPIError(
            method=GetChatMember(chat_id=chat_id, user_id=user_id), message="unavailable"
        )


class FakeStats:
    def __init__(self, totals):
        self._totals = totals

    async def totals(self, user_id, chat_ids):
        return self._totals


class ObservingParticipants:
    async def eligible_participant(self, campaign_id, user_id):
        return None

    async def begin_registration(self, request):
        return RegistrationAttempt(
            participant_id=uuid4(),
            qualification_check_id=uuid4(),
            required_channel_id=None,
            required_chat_ids=(THRESHOLD_CHAT_ID,),
            state=QualificationState.PENDING,
            requires_observation=True,
        )

    async def complete_registration(self, result):
        return SimpleNamespace(qualification_state=result.state, ticket_total_cache=0)

    async def pending_approval_chat_ids(self, participant_id):
        return frozenset()


class AlreadyEligibleParticipants:
    def __init__(self, total):
        self.total = total
        self.begun = 0

    async def eligible_participant(self, campaign_id, user_id):
        return SimpleNamespace(ticket_total_cache=self.total)

    async def begin_registration(self, request):
        self.begun += 1
        raise AssertionError("eligible participants must not register again")


class ReplayEligibleParticipants:
    def __init__(self):
        self.lookups = 0

    async def eligible_participant(self, campaign_id, user_id):
        self.lookups += 1
        return SimpleNamespace(ticket_total_cache=4) if self.lookups > 1 else None

    async def begin_registration(self, request):
        return RegistrationAttempt(
            participant_id=uuid4(),
            qualification_check_id=uuid4(),
            required_channel_id=None,
            required_chat_ids=(),
            state=QualificationState.ELIGIBLE,
            requires_observation=False,
        )


class CampaignQueries:
    def __init__(self, campaign):
        self._campaign = campaign

    async def campaign_by_reference(self, reference):
        return self._campaign

    async def campaign_group(self, campaign_id):
        return SimpleNamespace(chat_id=MAIN_CHAT_ID)


class RecordingMessage:
    def __init__(self):
        self.texts = []

    async def answer(self, text, *, reply_markup=None):
        self.texts.append(text)
        return SimpleNamespace(chat=SimpleNamespace(id=201), message_id=88)


class RecordingCallback:
    def __init__(self):
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def _campaign(message_thresholds):
    return SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        rules=CampaignRules(message_thresholds=message_thresholds).model_dump(mode="json"),
        presentation={
            "main_chat": {"id": MAIN_CHAT_ID, "title": "主群"},
            "required_chats": [{"id": THRESHOLD_CHAT_ID, "title": "发言群"}],
        },
    )


def _flow(participants, campaign, bot=None):
    return MemberFlow(
        bot=bot or FakeBot(),
        participants=participants,
        queries=CampaignQueries(campaign),
        cipher=object(),
        stats=FakeStats({}),
    )


def _user():
    return User(id=201, is_bot=False, first_name="Tester")


async def test_callback_unknown_state_answers_retry_alert():
    callback = RecordingCallback()
    flow = _flow(ObservingParticipants(), _campaign({}), bot=UnavailableBot())

    await flow.register(
        RecordingMessage(),
        PUBLIC_REF,
        user=_user(),
        observed_at=datetime.now(UTC),
        callback=callback,
    )

    assert len(callback.answers) == 1
    text, show_alert = callback.answers[0]
    assert text.startswith("当前无法核验群/频道资格")
    assert text.endswith("请稍后再试")
    assert show_alert is True


async def test_register_short_circuits_already_eligible_participant():
    participants = AlreadyEligibleParticipants(5)
    callback = RecordingCallback()
    message = RecordingMessage()
    flow = _flow(participants, _campaign({}))

    await flow.register(
        message, PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC), callback=callback
    )

    assert len(callback.answers) == 1
    text, show_alert = callback.answers[0]
    assert text.startswith("你已参与")
    assert "当前共 5 次抽奖机会" in text
    assert show_alert is False
    assert message.texts == []
    assert participants.begun == 0


async def test_register_already_eligible_private_start_replies_message():
    message = RecordingMessage()
    flow = _flow(AlreadyEligibleParticipants(6), _campaign({}))

    await flow.register(message, PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC))

    assert len(message.texts) == 1
    assert message.texts[0].startswith("你已参与")
    assert "当前共 6 次抽奖机会" in message.texts[0]


async def test_register_eligible_replay_reads_cached_total():
    callback = RecordingCallback()
    message = RecordingMessage()
    flow = _flow(ReplayEligibleParticipants(), _campaign({}))

    await flow.register(
        message,
        PUBLIC_REF,
        user=_user(),
        observed_at=datetime.now(UTC),
        callback=callback,
    )

    assert len(callback.answers) == 1
    text, show_alert = callback.answers[0]
    assert text.startswith("✅ 参与成功")
    assert "当前共 4 次抽奖机会" in text
    assert show_alert is False
    assert message.texts == []


async def test_thresholds_without_stats_service_raises_runtime_error():
    flow = MemberFlow(
        bot=FakeBot(),
        participants=ObservingParticipants(),
        queries=CampaignQueries(_campaign({str(THRESHOLD_CHAT_ID): 10})),
        cipher=object(),
    )

    with pytest.raises(RuntimeError, match="message stats service is required for thresholds"):
        await flow.register(
            RecordingMessage(), PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC)
        )


def test_popup_failure_lists_first_two_missing_groups_with_total():
    campaign = SimpleNamespace(
        presentation={
            "main_chat": {"id": MAIN_CHAT_ID, "title": "主群"},
            "required_chats": [
                {"id": -100201, "title": "群A"},
                {"id": -100202, "title": "群B"},
                {"id": -100203, "title": "群C"},
            ],
        }
    )
    observations = (
        {"chat_id": -100201, "state": "ineligible"},
        {"chat_id": -100202, "state": "ineligible"},
        {"chat_id": -100203, "state": "ineligible"},
    )

    text = popup_failure(campaign, QualificationState.INELIGIBLE, observations)

    assert text.startswith("请先加入")
    assert "群A" in text
    assert "群B" in text
    assert "群C" not in text
    assert text.endswith("等 3 个群")


def test_popup_failure_two_missing_groups_without_count_suffix():
    campaign = SimpleNamespace(
        presentation={
            "required_chats": [
                {"id": -100201, "title": "群A"},
                {"id": -100202, "title": "群B"},
            ]
        }
    )
    observations = (
        {"chat_id": -100201, "state": "ineligible"},
        {"chat_id": -100202, "state": "ineligible"},
    )

    text = popup_failure(campaign, QualificationState.INELIGIBLE, observations)

    assert text.startswith("请先加入")
    assert text.endswith("群B")
    assert "个群" not in text
