from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.types import CallbackQuery, Message

from tests.navigation_helpers import PassthroughAdmin, PassthroughNavigation
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.update_handler import LotteryUpdateHandler, _public_chat_title


class RecordingMemberFlow:
    def __init__(self) -> None:
        self._bot = AsyncMock()
        self.user_id: int | None = None
        self.observed_at: datetime | None = None
        self.campaign_reference: str | None = None
        self.action: str | None = None
        self.position: int | None = None
        self.missing_page = 1
        self.pending_requests: list[dict[str, object]] = []

    async def register(
        self,
        message,
        campaign_reference,
        *,
        user=None,
        observed_at=None,
        callback=None,
        missing_page=1,
    ) -> None:
        self.campaign_reference = campaign_reference
        self.user_id = user.id
        self.observed_at = observed_at
        self.missing_page = missing_page

    async def join_request_pending(self, **values) -> None:
        self.pending_requests.append(values)

    async def show_rules(self, message, campaign_reference) -> None:
        self.action = "rules"
        self.campaign_reference = campaign_reference

    async def show_condition(self, message, campaign_reference, position) -> None:
        self.action = "condition"
        self.campaign_reference = campaign_reference
        self.position = position

    async def show_invite_link(self, message, campaign_reference, *, user_id, page=0) -> None:
        self.action = "invite_link"
        self.invite_chat = (message.chat.id, message.chat.type)
        self.campaign_reference = campaign_reference
        self.user_id = user_id


class RecordingAdminFlow:
    def __init__(self) -> None:
        self.started = False
        self.campaign_reference = None

    async def suspend_draft(self, user_id):
        return None

    async def start_create_message(self, message) -> None:
        self.started = True

    async def show_campaign_reference(self, query, reference) -> None:
        self.campaign_reference = reference


class PassthroughPersonalFlow:
    def __init__(self) -> None:
        self.index_shown = 0

    async def handle_callback(self, query, data) -> bool:
        return False

    async def handle_command(self, message, text) -> bool:
        return False

    async def show_index(self, message) -> None:
        self.index_shown += 1


class RecordingMessage:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class FakeStats:
    def __init__(self) -> None:
        self.recorded: list = []

    async def record(self, observation) -> None:
        self.recorded.append(observation)


class RecordingActivity:
    def __init__(self) -> None:
        self.recorded: list = []

    async def record_message(self, observation) -> None:
        self.recorded.append(observation)


async def test_verify_callback_registers_clicking_user_not_bot_message_sender() -> None:
    flow = RecordingMemberFlow()
    handler = LotteryUpdateHandler(
        navigation=PassthroughNavigation(),
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=PassthroughAdmin(),
        member_flow=flow,
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=SimpleNamespace(campaign_by_reference=AsyncMock(return_value=None)),
        stats=FakeStats(),
    )
    public_ref = "AbCdEfGhIjKlMnOpQrSt-_"
    query = _callback(f"member:verify:{public_ref}")
    await handler._dispatch_callback(query, query.data)
    assert flow.campaign_reference == public_ref
    assert flow.user_id == 201
    assert flow.observed_at is not None


async def test_legacy_verify_callback_remains_accepted() -> None:
    flow = RecordingMemberFlow()
    handler = _handler(member_flow=flow)
    legacy_ref = str(uuid4())
    query = _callback(f"member:verify:{legacy_ref}")

    await handler._dispatch_callback(query, query.data)

    assert flow.campaign_reference == legacy_ref
    assert flow.user_id == 201


async def test_missing_targets_page_callback_preserves_requested_page() -> None:
    flow = RecordingMemberFlow()
    handler = _handler(member_flow=flow)
    public_ref = "AbCdEfGhIjKlMnOpQrSt-_"
    query = _callback(f"member:missing:{public_ref}:2")

    await handler._dispatch_callback(query, query.data)

    assert flow.campaign_reference == public_ref
    assert flow.missing_page == 2


async def test_join_request_projects_waiting_approval_without_personal_invite() -> None:
    flow = RecordingMemberFlow()
    campaign_id = uuid4()
    queries = SimpleNamespace(campaigns_for_mandatory_chat=AsyncMock(return_value=(campaign_id,)))
    handler = LotteryUpdateHandler(
        navigation=PassthroughNavigation(),
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=PassthroughAdmin(),
        member_flow=flow,
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=queries,
        stats=FakeStats(),
    )
    observed_at = datetime.now(UTC)
    event = SimpleNamespace(
        invite_link=None,
        from_user=SimpleNamespace(id=201, is_bot=False),
        chat=SimpleNamespace(id=-100123),
        date=observed_at,
    )

    await handler._join_request(event)

    assert flow.pending_requests == [
        {
            "campaign_id": campaign_id,
            "user": event.from_user,
            "chat_id": -100123,
            "observed_at": observed_at,
        }
    ]


async def test_rules_and_condition_start_payloads_are_distinct_from_registration() -> None:
    flow = RecordingMemberFlow()
    handler = _handler(member_flow=flow)
    public_ref = "AbCdEfGhIjKlMnOpQrSt-_"

    assert await handler._start_payload(object(), f"rules_{public_ref}") is True
    assert flow.action == "rules"
    assert flow.user_id is None


async def test_condition_callback_opens_the_selected_position() -> None:
    flow = RecordingMemberFlow()
    handler = _handler(member_flow=flow)
    public_ref = "AbCdEfGhIjKlMnOpQrSt-_"
    query = _callback(f"member:condition:{public_ref}:3")

    await handler._dispatch_callback(query, query.data)

    assert flow.action == "condition"
    assert flow.campaign_reference == public_ref
    assert flow.position == 3
    assert await handler._start_payload(object(), f"condition_{public_ref}_2") is True
    assert flow.action == "condition"
    assert flow.position == 2
    assert flow.user_id is None


async def test_campaign_invite_callback_is_scoped_to_campaign_and_clicking_user() -> None:
    flow = RecordingMemberFlow()
    handler = _handler(member_flow=flow)
    public_ref = "AbCdEfGhIjKlMnOpQrSt-_"
    query = _callback(f"member:invite:{public_ref}").as_(flow._bot)

    await handler._dispatch_callback(query, query.data)

    assert flow.action == "invite_link"
    assert flow.campaign_reference == public_ref
    assert flow.user_id == 201
    assert flow.invite_chat == (201, "private")


def test_missing_chat_title_never_falls_back_to_raw_chat_id() -> None:
    assert _public_chat_title(None) == "未命名群组/频道"
    assert _public_chat_title("  ") == "未命名群组/频道"
    assert _public_chat_title(" 活动群 ") == "活动群"


async def test_new_command_enters_creation_directly() -> None:
    admin_flow = RecordingAdminFlow()
    handler = LotteryUpdateHandler(
        navigation=PassthroughNavigation(),
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=admin_flow,
        member_flow=object(),
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=SimpleNamespace(campaign_by_reference=AsyncMock(return_value=None)),
        stats=FakeStats(),
    )
    message = type(
        "PrivateMessage", (), {"text": "/new", "from_user": SimpleNamespace(id=201, is_bot=False)}
    )()

    await handler._private_message(message)

    assert admin_flow.started is True


async def test_menu_command_redirects_to_unified_index() -> None:
    personal = PassthroughPersonalFlow()
    handler = LotteryUpdateHandler(
        navigation=PassthroughNavigation(),
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=RecordingAdminFlow(),
        member_flow=object(),
        personal_flow=personal,
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=SimpleNamespace(campaign_by_reference=AsyncMock(return_value=None)),
        stats=FakeStats(),
    )

    await handler._private_message(
        type("PrivateMessage", (), {"text": "/menu", "from_user": SimpleNamespace(id=201)})()
    )

    assert personal.index_shown == 1


async def test_campaign_list_item_opens_authorized_campaign_detail() -> None:
    admin_flow = RecordingAdminFlow()
    handler = _handler_with_admin(admin_flow)
    query = _callback("nav:campaign:AbCdEfGhIjKlMnOpQrSt-_")

    await handler._dispatch_callback(query, query.data)

    assert admin_flow.campaign_reference == "AbCdEfGhIjKlMnOpQrSt-_"


async def test_internal_error_is_logged_but_not_exposed(caplog) -> None:
    handler = _handler()
    message = RecordingMessage()
    secret = f"unknown callback action: {uuid4()}"

    await handler._send_operation_error(message, ValueError(secret))

    assert message.answers == ["操作未完成: 系统暂时无法完成此操作, 请稍后重试。"]
    assert secret not in message.answers[0]
    assert secret in caplog.text


class _RecordingCallbackQuery:
    def __init__(self, data: str, message: Message) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=201)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class RaisingMemberFlow:
    async def register_callback(self, query, reference) -> None:
        raise UserFacingError("活动不存在或已经失效")


def _group_card_message() -> Message:
    return Message.model_validate(
        {
            "message_id": 10,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": -100888, "type": "supergroup"},
            "from": {"id": 123, "is_bot": True, "first_name": "Bot"},
            "text": "campaign card",
        }
    )


async def test_callback_error_answers_alert_without_group_message() -> None:
    handler = _handler(member_flow=RaisingMemberFlow())
    message = _group_card_message()
    query = _RecordingCallbackQuery("member:join:AbCdEfGhIjKlMnOpQrSt-_", message)
    reported: list[tuple[Message, Exception]] = []

    async def record_error(msg: Message, exc: Exception) -> None:
        reported.append((msg, exc))

    handler._send_operation_error = record_error

    await handler._callback(query)

    assert query.answers == [("活动不存在或已经失效", True)]
    assert reported == []


async def test_reviewed_validation_message_remains_actionable() -> None:
    handler = _handler()
    message = RecordingMessage()

    await handler._send_operation_error(message, UserFacingError("奖品名称长度必须为 1-120 字"))

    assert message.answers == ["操作未完成: 奖品名称长度必须为 1-120 字"]


def _handler(*, member_flow=None) -> LotteryUpdateHandler:
    return LotteryUpdateHandler(
        navigation=PassthroughNavigation(),
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=PassthroughAdmin(),
        member_flow=member_flow or object(),
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=SimpleNamespace(campaign_by_reference=AsyncMock(return_value=None)),
        stats=FakeStats(),
    )


def _handler_with_admin(admin_flow) -> LotteryUpdateHandler:
    handler = _handler()
    handler._admin_flow = admin_flow
    return handler


def _callback(data: str) -> CallbackQuery:
    timestamp = int(datetime.now(UTC).timestamp())
    return CallbackQuery.model_validate(
        {
            "id": "callback-1",
            "from": {"id": 201, "is_bot": False, "first_name": "Human"},
            "chat_instance": "private-chat",
            "data": data,
            "message": {
                "message_id": 10,
                "date": timestamp,
                "chat": {"id": 201, "type": "private"},
                "from": {"id": 123, "is_bot": True, "first_name": "Lottery Bot"},
                "text": "尚未满足频道关注条件",
            },
        }
    )


@pytest.fixture
def fake_bot():
    return object()


@pytest.fixture
def make_handler(fake_bot):
    def _make(*, stats=None, bot_username="") -> LotteryUpdateHandler:
        return LotteryUpdateHandler(
            navigation=PassthroughNavigation(),
            bot=fake_bot,
            default_timezone="Asia/Shanghai",
            admin_flow=PassthroughAdmin(),
            member_flow=object(),
            personal_flow=PassthroughPersonalFlow(),
            tokens=object(),
            groups=object(),
            activity=RecordingActivity(),
            invitations=object(),
            queries=SimpleNamespace(campaign_by_reference=AsyncMock(return_value=None)),
            stats=stats or FakeStats(),
            bot_username=bot_username,
        )

    return _make


def _group_message(text=None, chat_id=-100123, user_id=42):
    return SimpleNamespace(
        migrate_to_chat_id=None,
        chat=SimpleNamespace(id=chat_id, type="supergroup"),
        from_user=SimpleNamespace(id=user_id, is_bot=False),
        message_id=1,
        date=datetime.now(UTC),
        text=text,
        caption=None,
        photo=None,
        sticker=None,
        animation=None,
        forward_origin=None,
        answer=AsyncMock(),
    )


async def test_group_message_records_stats(fake_bot, make_handler):
    stats = FakeStats()
    handler = make_handler(stats=stats)
    message = _group_message(text="今天天气不错哦", chat_id=-100123, user_id=42)
    await handler._message(message)
    assert [o.text for o in stats.recorded] == ["今天天气不错哦"]
    assert handler._activity.recorded == stats.recorded


async def test_group_command_skips_stats_and_answers_getid(fake_bot, make_handler):
    stats = FakeStats()
    handler = make_handler(stats=stats, bot_username="tgccjbot")
    message = _group_message(text="/getid", chat_id=-100123, user_id=42)
    await handler._message(message)
    assert stats.recorded == []
    message.answer.assert_awaited_once()
    assert "-100123" in message.answer.await_args.args[0]
    assert "以 -100 开头" in message.answer.await_args.args[0]


async def test_group_private_command_guides_to_dm(fake_bot, make_handler):
    handler = make_handler(stats=FakeStats(), bot_username="tgccjbot")
    message = _group_message(text="/region@tgccjbot", chat_id=-100123, user_id=42)
    await handler._message(message)
    text = message.answer.await_args.args[0]
    assert "私聊" in text
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    button = keyboard.inline_keyboard[0][0]
    assert button.url == "https://t.me/tgccjbot"


async def test_group_command_mentioning_other_bot_is_ignored(fake_bot, make_handler):
    stats = FakeStats()
    handler = make_handler(stats=stats, bot_username="tgccjbot")
    message = _group_message(text="/region@otherbot", chat_id=-100123, user_id=42)
    await handler._message(message)
    message.answer.assert_not_awaited()
    assert stats.recorded == []
