from datetime import UTC, datetime
from uuid import uuid4

from aiogram.types import CallbackQuery

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.update_handler import LotteryUpdateHandler, _public_chat_title


class RecordingMemberFlow:
    def __init__(self) -> None:
        self.user_id: int | None = None
        self.observed_at: datetime | None = None
        self.campaign_reference: str | None = None
        self.action: str | None = None
        self.position: int | None = None

    async def register(self, message, campaign_reference, *, user=None, observed_at=None) -> None:
        self.campaign_reference = campaign_reference
        self.user_id = user.id
        self.observed_at = observed_at

    async def show_rules(self, message, campaign_reference) -> None:
        self.action = "rules"
        self.campaign_reference = campaign_reference

    async def show_condition(self, message, campaign_reference, position) -> None:
        self.action = "condition"
        self.campaign_reference = campaign_reference
        self.position = position

    async def show_invite_link(self, message, campaign_reference, *, user_id) -> None:
        self.action = "invite_link"
        self.campaign_reference = campaign_reference
        self.user_id = user_id


class RecordingAdminFlow:
    def __init__(self) -> None:
        self.started = False
        self.campaign_reference = None

    async def start_create_message(self, message) -> None:
        self.started = True

    async def show_campaign_reference(self, query, reference) -> None:
        self.campaign_reference = reference


class PassthroughPersonalFlow:
    async def handle_callback(self, query, data) -> bool:
        return False


class RecordingMessage:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


async def test_verify_callback_registers_clicking_user_not_bot_message_sender() -> None:
    flow = RecordingMemberFlow()
    handler = LotteryUpdateHandler(
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=object(),
        member_flow=flow,
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=object(),
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
    query = _callback(f"member:invite:{public_ref}")

    await handler._dispatch_callback(query, query.data)

    assert flow.action == "invite_link"
    assert flow.campaign_reference == public_ref
    assert flow.user_id == 201


def test_missing_chat_title_never_falls_back_to_raw_chat_id() -> None:
    assert _public_chat_title(None) == "未命名群组/频道"
    assert _public_chat_title("  ") == "未命名群组/频道"
    assert _public_chat_title(" 活动群 ") == "活动群"


async def test_new_command_enters_creation_directly() -> None:
    admin_flow = RecordingAdminFlow()
    handler = LotteryUpdateHandler(
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=admin_flow,
        member_flow=object(),
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=object(),
    )
    message = type("PrivateMessage", (), {"text": "/new"})()

    await handler._private_message(message)

    assert admin_flow.started is True


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


async def test_reviewed_validation_message_remains_actionable() -> None:
    handler = _handler()
    message = RecordingMessage()

    await handler._send_operation_error(message, UserFacingError("奖品名称长度必须为 1-120 字"))

    assert message.answers == ["操作未完成: 奖品名称长度必须为 1-120 字"]


def _handler(*, member_flow=None) -> LotteryUpdateHandler:
    return LotteryUpdateHandler(
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=object(),
        member_flow=member_flow or object(),
        personal_flow=PassthroughPersonalFlow(),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=object(),
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
