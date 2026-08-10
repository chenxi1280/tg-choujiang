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

    async def register(self, message, campaign_reference, *, user=None, observed_at=None) -> None:
        self.campaign_reference = campaign_reference
        self.user_id = user.id
        self.observed_at = observed_at


class RecordingAdminFlow:
    def __init__(self) -> None:
        self.started = False

    async def start_create_message(self, message) -> None:
        self.started = True


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
