from datetime import UTC, datetime
from uuid import uuid4

from aiogram.types import CallbackQuery

from tg_lottery.telegram.update_handler import LotteryUpdateHandler


class RecordingMemberFlow:
    def __init__(self) -> None:
        self.user_id: int | None = None
        self.observed_at: datetime | None = None

    async def register(self, message, campaign_id, *, user=None, observed_at=None) -> None:
        self.user_id = user.id
        self.observed_at = observed_at


async def test_verify_callback_registers_clicking_user_not_bot_message_sender() -> None:
    flow = RecordingMemberFlow()
    handler = LotteryUpdateHandler(
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=object(),
        member_flow=flow,
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=object(),
    )
    query = _callback(f"member:verify:{uuid4()}")
    await handler._dispatch_callback(query, query.data)
    assert flow.user_id == 201
    assert flow.observed_at is not None


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
