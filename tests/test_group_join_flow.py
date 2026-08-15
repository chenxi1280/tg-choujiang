from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from aiogram.types import User

from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_flow import MemberFlow

PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"
MAIN_CHAT_ID = -100888
THRESHOLD_CHAT_ID = -100999


class FakeBot:
    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="member")


class FakeStats:
    def __init__(self, totals):
        self._totals = totals

    async def totals(self, user_id, chat_ids):
        return self._totals


class ObservingParticipants:
    def __init__(self):
        self.result = None

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
        self.result = result
        return SimpleNamespace(qualification_state=result.state)


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


def _flow(participants, campaign, totals):
    return MemberFlow(
        bot=FakeBot(),
        participants=participants,
        queries=CampaignQueries(campaign),
        cipher=object(),
        stats=FakeStats(totals),
    )


def _user():
    return User(id=201, is_bot=False, first_name="Tester")


async def test_register_fails_when_message_threshold_unmet():
    campaign = _campaign({str(THRESHOLD_CHAT_ID): 10})
    participants = ObservingParticipants()
    flow = _flow(participants, campaign, {THRESHOLD_CHAT_ID: 3})

    await flow.register(
        RecordingMessage(), PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC)
    )

    assert participants.result.state is QualificationState.INELIGIBLE
    shortfall = next(
        item
        for item in participants.result.observations
        if item["error_code"] == "message_threshold"
    )
    assert shortfall["chat_id"] == THRESHOLD_CHAT_ID
    assert shortfall["state"] == "ineligible"
    assert shortfall["observed_status"] == "messages 3/10"


async def test_register_success_keeps_eligible_when_threshold_met():
    campaign = _campaign({str(THRESHOLD_CHAT_ID): 10})
    participants = ObservingParticipants()
    flow = _flow(participants, campaign, {THRESHOLD_CHAT_ID: 12})

    await flow.register(
        RecordingMessage(), PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC)
    )

    assert participants.result.state is QualificationState.ELIGIBLE
    assert all(
        item["error_code"] != "message_threshold"
        for item in participants.result.observations
    )


async def test_callback_success_popup_answers_participation_success():
    campaign = _campaign({str(THRESHOLD_CHAT_ID): 10})
    callback = RecordingCallback()
    flow = _flow(ObservingParticipants(), campaign, {THRESHOLD_CHAT_ID: 12})

    await flow.register(
        RecordingMessage(),
        PUBLIC_REF,
        user=_user(),
        observed_at=datetime.now(UTC),
        callback=callback,
    )

    assert len(callback.answers) == 1
    text, show_alert = callback.answers[0]
    assert "参与成功" in text
    assert show_alert is False


async def test_callback_failure_popup_alerts_threshold_gap():
    campaign = _campaign({str(THRESHOLD_CHAT_ID): 10})
    callback = RecordingCallback()
    flow = _flow(ObservingParticipants(), campaign, {THRESHOLD_CHAT_ID: 3})

    await flow.register(
        RecordingMessage(),
        PUBLIC_REF,
        user=_user(),
        observed_at=datetime.now(UTC),
        callback=callback,
    )

    assert len(callback.answers) == 1
    text, show_alert = callback.answers[0]
    assert show_alert is True
    assert "「发言群」发言 3/10 条" in text
    assert "还差 7 条" in text
