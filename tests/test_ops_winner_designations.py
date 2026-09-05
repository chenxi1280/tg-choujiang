from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from aiogram.enums import ChatType

from tg_lottery.application.winner_designations import (
    DesignationCampaignView,
    WinnerDesignationView,
)
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import PollingOffset
from tg_lottery.ops_bot import _require_isolated_bot_id
from tg_lottery.telegram.ops_winner_designations import OpsWinnerDesignationHandler


class RecordingMessage:
    def __init__(self, text: str, *, user_id: int = 101, chat_type=ChatType.PRIVATE) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id, is_bot=False)
        self.chat = SimpleNamespace(type=chat_type)
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def require_authorized(self, actor_user_id):
        if actor_user_id != 101:
            raise UserFacingError("无权访问此运维入口: 操作人不在中奖指定授权名单中")

    async def editable_campaigns(self, *, actor_user_id):
        return (DesignationCampaignView(uuid4(), "ABC123XYZ0", "隔离测试", CampaignState.RUNNING),)

    async def list(self, campaign_id, *, actor_user_id):
        self.calls.append(("list", {"campaign_id": campaign_id}))
        return ()

    async def set(self, **kwargs):
        self.calls.append(("set", kwargs))
        return WinnerDesignationView(1, "一等奖", 1, 202, "chosen", "指定成员")

    async def remove(self, **kwargs):
        self.calls.append(("remove", kwargs))
        return True


class RecordingQueries:
    def __init__(self) -> None:
        self.campaign = SimpleNamespace(id=uuid4(), name="隔离测试", public_ref="ABC123XYZ0")

    async def campaign_by_reference(self, reference):
        return self.campaign if reference == self.campaign.public_ref else None

    async def prizes(self, campaign_id):
        return [SimpleNamespace(position=1, name="一等奖", winner_count=1)]


def _handler(service=None, queries=None) -> OpsWinnerDesignationHandler:
    return OpsWinnerDesignationHandler(
        service=service or RecordingService(),
        queries=queries or RecordingQueries(),
        source_bot_id=9001,
    )


async def test_authorized_private_command_writes_designation_with_ops_bot_source() -> None:
    service = RecordingService()
    message = RecordingMessage("/winner_set ABC123XYZ0 1 1 @chosen")

    await _handler(service=service).handle(message)

    action, values = service.calls[0]
    assert action == "set"
    assert values["actor_user_id"] == 101
    assert values["source_bot_id"] == 9001
    assert len(message.answers) == 1
    assert "已写入后台指定" in message.answers[0]
    assert "@chosen" in message.answers[0]


async def test_ops_bot_rejects_non_allowlisted_private_user() -> None:
    service = RecordingService()
    message = RecordingMessage("/campaigns", user_id=999)

    await _handler(service=service).handle(message)

    assert service.calls == []
    assert len(message.answers) == 1
    assert "无权访问此运维入口: 操作人不在中奖指定授权名单中" in message.answers[0]


async def test_ops_bot_never_responds_in_groups() -> None:
    service = RecordingService()
    message = RecordingMessage("/campaigns", chat_type=ChatType.SUPERGROUP)

    await _handler(service=service).handle(message)

    assert service.calls == []
    assert message.answers == []


async def test_ops_bot_lists_campaigns_without_exposing_command_in_online_bot() -> None:
    message = RecordingMessage("/campaigns")

    await _handler().handle(message)

    assert "隔离测试 · ABC123XYZ0 · 进行中" in message.answers[0]


async def test_ops_bot_identity_cannot_reuse_online_polling_bot(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        session.add(
            PollingOffset(
                bot_id=9001,
                last_durable_update_id=1,
                last_poll_succeeded_at=now,
                updated_at=now,
            )
        )

    with pytest.raises(RuntimeError, match="must not reuse"):
        await _require_isolated_bot_id(session_factory, 9001)

    await _require_isolated_bot_id(session_factory, 9002)
