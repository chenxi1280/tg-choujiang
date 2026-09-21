from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.types import Message

from tg_lottery.domain.errors import RecoverableDraftCallback
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


class ExpiredDraftTokens:
    async def consume(self, token: str, *, owner_user_id: int, chat_id: int):
        raise RecoverableDraftCallback("callback token is expired")


class RecoveringAdminFlow:
    def __init__(self) -> None:
        self.recovered = False

    async def recover_draft_callback(self, query) -> bool:
        self.recovered = True
        return True


class RecordingQuery:
    def __init__(self, message: Message) -> None:
        self.message = message
        self.from_user = SimpleNamespace(id=201)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


async def test_expired_draft_callback_refreshes_active_panel() -> None:
    admin = RecoveringAdminFlow()
    handler = object.__new__(LotteryUpdateHandler)
    handler._tokens = ExpiredDraftTokens()
    handler._admin_flow = admin
    query = RecordingQuery(_private_bot_message())

    await handler._dispatch_secure_callback(query, "secure:expired")

    assert admin.recovered is True
    assert "最新草稿面板" in query.answers[0][0]
    assert query.answers[0][1] is True


def _private_bot_message() -> Message:
    return Message.model_validate(
        {
            "message_id": 10,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": 201, "type": "private"},
            "from": {"id": 123, "is_bot": True, "first_name": "Bot"},
            "text": "draft panel",
        }
    )
