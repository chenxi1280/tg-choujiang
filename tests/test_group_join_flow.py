from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, User

from tests.navigation_helpers import PassthroughAdmin, PassthroughNavigation
from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.update_handler import LotteryUpdateHandler

PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"
MAIN_CHAT_ID = -100888
THRESHOLD_CHAT_ID = -100999


class FakeBot:
    def __init__(self):
        self.sent = []

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="member")

    async def send_message(self, chat_id, text, *, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=99)


class FakeStats:
    def __init__(self, totals):
        self._totals = totals

    async def totals(self, user_id, chat_ids):
        return self._totals


class ObservingParticipants:
    def __init__(self):
        self.result = None
        self.begun = 0

    async def eligible_participant(self, campaign_id, user_id):
        return None

    async def begin_registration(self, request):
        self.begun += 1
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
        return SimpleNamespace(qualification_state=result.state, ticket_total_cache=3)

    async def pending_approval_chat_ids(self, participant_id):
        return frozenset()


class CampaignQueries:
    async def participant_campaign_detail(self, user_id, reference):
        return None

    def __init__(self, campaign):
        self._campaign = campaign

    async def campaign_by_reference(self, reference):
        return self._campaign

    async def campaign_group(self, campaign_id):
        return SimpleNamespace(chat_id=MAIN_CHAT_ID)


class RecordingMessage:
    chat = SimpleNamespace(type="private", id=201)

    def __init__(self):
        self.texts = []
        self.markups = []

    async def answer(self, text, *, reply_markup=None):
        self.texts.append(text)
        self.markups.append(reply_markup)
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


def _flow(participants, campaign, totals, *, bot=None):
    return MemberFlow(
        bot=bot or FakeBot(),
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

    await flow.register(RecordingMessage(), PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC))

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

    await flow.register(RecordingMessage(), PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC))

    assert participants.result.state is QualificationState.ELIGIBLE
    assert all(
        item["error_code"] != "message_threshold" for item in participants.result.observations
    )


async def test_group_registration_click_opens_private_same_activity_without_registering():
    campaign = _campaign({str(THRESHOLD_CHAT_ID): 10})
    callback = SimpleNamespace(answer=AsyncMock())
    message = RecordingMessage()
    message.chat = SimpleNamespace(id=MAIN_CHAT_ID, type="supergroup")
    participants = ObservingParticipants()
    flow = _flow(participants, campaign, {THRESHOLD_CHAT_ID: 12})
    await flow.register(
        message, PUBLIC_REF, user=_user(), observed_at=datetime.now(UTC), callback=callback
    )
    assert callback.answer.await_args.kwargs["url"].endswith("?start=join_" + PUBLIC_REF)
    assert participants.begun == 0 and participants.result is None
    assert message.texts == []


class SendingBot:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="member")

    async def send_message(self, chat_id, text, *, reply_markup=None):
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=99)


class BonusQueries:
    def __init__(self, campaign, link):
        self._campaign = campaign
        self._link = link

    async def campaign_by_reference(self, reference):
        return self._campaign

    async def campaign_group(self, campaign_id):
        return SimpleNamespace(chat_id=MAIN_CHAT_ID)

    async def invite_link_for_participant(self, campaign_id, user_id):
        return self._link

    async def participant_campaign_detail(self, user_id, reference):
        return SimpleNamespace(qualification=QualificationState.ELIGIBLE) if self._link else None


class PlainCipher:
    def decrypt(self, encrypted, *, context):
        return "https://t.me/+personal_link"


class FakeQuery:
    def __init__(self, message):
        self.message = message
        self.from_user = _user()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class RoutingMemberFlow:
    def __init__(self):
        self._bot = Bot(token="123456:test-token", session=AsyncMock(return_value=True))
        self.calls = []

    async def register_callback(self, query, ref):
        self.calls.append((query, ref))

    async def show_tickets(self, message, user_id):
        pass


class PassthroughPersonalFlow:
    async def handle_callback(self, query, data):
        return False

    async def show_joined(self, message):
        return None


class InviteStartFlow:
    def __init__(self):
        self.calls = []

    async def show_invite_link(self, message, ref, *, user_id):
        self.calls.append((message, ref, user_id))


def _handler(member_flow):
    return LotteryUpdateHandler(
        navigation=PassthroughNavigation(),
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=PassthroughAdmin(),
        member_flow=member_flow,
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=object(),
        stats=FakeStats({}),
    )


def _group_callback(data):
    return CallbackQuery.model_validate(
        {
            "id": "cb-1",
            "from": {"id": 201, "is_bot": False, "first_name": "Human"},
            "chat_instance": "group",
            "data": data,
            "message": {
                "message_id": 10,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": MAIN_CHAT_ID, "type": "supergroup"},
                "from": {"id": 123, "is_bot": True, "first_name": "Bot"},
                "text": "campaign card",
            },
        }
    )


def _pre_answer_spy(handler):
    preanswered = []

    async def spy(query):
        preanswered.append(query)

    handler._answer_callback = spy
    return preanswered


def _bonus_campaign():
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        name="加成测试抽奖",
        state=CampaignState.RUNNING,
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=1),
        rules=CampaignRules(bonus_types=("invite",)).model_dump(mode="json"),
        presentation={},
    )


def _bonus_flow(campaign, link, error=None):
    bot = SendingBot(error=error)
    flow = MemberFlow(
        bot=bot,
        participants=object(),
        queries=BonusQueries(campaign, link),
        cipher=PlainCipher(),
        bot_username="tgccjbot",
    )
    return flow, bot


async def test_group_join_callback_routes_to_register_callback_without_preanswer():
    flow = RoutingMemberFlow()
    handler = _handler(flow)
    preanswered = _pre_answer_spy(handler)
    query = _group_callback(f"member:join:{PUBLIC_REF}")

    await handler._callback(query)

    assert flow.calls == [(query, PUBLIC_REF)]
    assert preanswered == []


async def test_legacy_record_callback_acknowledges_after_dispatch_without_preanswer():
    flow = RoutingMemberFlow()
    handler = _handler(flow)
    preanswered = _pre_answer_spy(handler)

    await handler._callback(_group_callback("member:tickets"))

    assert preanswered == []
    assert flow.calls == []
    assert isinstance(flow._bot.session.call_args.args[1], AnswerCallbackQuery)


async def test_bonus_callback_without_registration_answers_popup():
    flow, _ = _bonus_flow(_bonus_campaign(), None)
    query = FakeQuery(RecordingMessage())

    await flow.bonus_callback(query, PUBLIC_REF)

    assert len(query.answers) == 1
    text, show_alert = query.answers[0]
    assert show_alert is True
    assert "参与抽奖" in text
    assert "立即参与" not in text


async def test_bonus_callback_sends_invite_link_to_private_chat():
    link = SimpleNamespace(id=uuid4(), encrypted_url=b"enc", revoked_at=None, expire_at=None)
    flow, bot = _bonus_flow(_bonus_campaign(), link)
    query = FakeQuery(RecordingMessage())

    await flow.bonus_callback(query, PUBLIC_REF)

    assert len(bot.sent) == 1
    chat_id, text, _ = bot.sent[0]
    assert chat_id == 201
    assert "专属邀请链接" in text
    assert query.answers == [("加成进度已私聊发送给你", False)]


async def test_bonus_callback_private_failure_guides_user_to_dm():
    link = SimpleNamespace(id=uuid4(), encrypted_url=b"enc", revoked_at=None, expire_at=None)
    error = TelegramForbiddenError(
        method=SendMessage(chat_id=201, text="x"), message="Forbidden: bot blocked"
    )
    flow, _ = _bonus_flow(_bonus_campaign(), link, error=error)
    message = RecordingMessage()
    query = FakeQuery(message)

    await flow.bonus_callback(query, PUBLIC_REF)

    assert message.texts == []
    assert "请先私聊机器人" in query.answers[-1][0]
    assert query.answers[-1][1] is True


async def test_start_invite_payload_delegates_to_show_invite_link():
    flow = InviteStartFlow()
    handler = _handler(flow)
    message = SimpleNamespace(text=f"/start invite_{PUBLIC_REF}", from_user=SimpleNamespace(id=201))

    await handler._private_message(message)

    assert flow.calls == [(message, PUBLIC_REF, 201)]
