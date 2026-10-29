from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import Participant, TelegramOutbox, TicketLedger
from tg_lottery.telegram.gateway_lottery_query import deliver_query
from tg_lottery.telegram.lottery_queries import LotteryQueries, query_kind


def message(text, *, chat_id=-100123, user_id=901, number=55):
    return Message(
        message_id=number,
        date=datetime.now(UTC),
        text=text,
        from_user=User(id=user_id, is_bot=False, first_name="member"),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
        message_thread_id=19,
    )


@pytest.mark.parametrize(
    "text",
    [
        "抽奖进度",
        "发言进度",
        "门槛进度",
        "/抽奖进度",
        "/发言进度",
        "/门槛进度",
        "/progress@TestBot",
    ],
)
def test_progress_aliases(text):
    assert query_kind(text, "testbot") == "progress"


@pytest.mark.parametrize(
    "text", ["/抽奖@otherbot", "我的抽奖进度呢", "/progress other", "抽奖进度abc"]
)
def test_unrelated_input_is_not_a_query(text):
    assert query_kind(text, "testbot") is None


async def test_public_discovery_is_blue_link_without_registration(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        reference = campaign.public_ref
    flow = LotteryQueries(QueryService(session_factory), bot_username="testbot")
    assert await flow.message(message("/抽奖"), "/抽奖")
    assert await flow.message(message("/抽奖"), "/抽奖")
    async with session_factory() as session:
        rows = list(await session.scalars(select(TelegramOutbox)))
        assert len(rows) == 1
        assert f'?start=join_{reference}">测试活动</a>' in rows[0].payload["text"]
        assert "delete_after_seconds" not in rows[0].payload
        assert await session.scalar(select(func.count()).select_from(Participant)) == 1
        assert await session.scalar(select(func.count()).select_from(TicketLedger)) == 0


async def test_empty_rate_limited_and_error_progress_all_delete_after_twenty(
    session_factory, monkeypatch
):
    flow = LotteryQueries(QueryService(session_factory), bot_username="testbot")
    await flow.message(message("抽奖进度", number=1), "抽奖进度")
    await flow.message(message("发言进度", number=2), "发言进度")
    monkeypatch.setattr(flow, "_view", AsyncMock(side_effect=ValueError("projection unavailable")))
    await flow.message(message("门槛进度", number=3, user_id=902), "门槛进度")
    async with session_factory() as session:
        rows = list(
            await session.scalars(select(TelegramOutbox).order_by(TelegramOutbox.created_at))
        )
        assert len(rows) == 3
        assert all(row.payload["delete_after_seconds"] == 20 for row in rows)
        assert "暂无" in rows[0].payload["text"]
        assert "频繁" in rows[1].payload["text"]
        assert "暂时无法" in rows[2].payload["text"]
        assert all(row.payload["request_id"] in (1, 2, 3) for row in rows)


async def test_query_reply_preserves_topic_and_original_command():
    bot = SimpleNamespace(send_message=AsyncMock(return_value=message("reply")))
    request = SimpleNamespace(
        recipient_chat_id=-100123,
        payload={
            "text": "📊 抽奖进度",
            "thread_id": 19,
            "request_id": 77,
            "reply_markup": {"inline_keyboard": []},
        },
    )
    await deliver_query(bot, request)
    options = bot.send_message.await_args.kwargs
    assert options["reply_parameters"].message_id == 77
    assert options["message_thread_id"] == 19


def test_progress_callback_is_bound_to_requester_and_message():
    source = SimpleNamespace(
        operation="send_lottery_query",
        state=OutboxState.CONFIRMED,
        recipient_chat_id=-100123,
        remote_message_id=55,
        payload={"kind": "progress", "owner": 901},
    )
    query = SimpleNamespace(message=message("reply"), from_user=SimpleNamespace(id=902))
    assert not LotteryQueries._valid_source(source, query)
    query.from_user.id = 901
    assert LotteryQueries._valid_source(source, query)
    source.remote_message_id = 56
    assert not LotteryQueries._valid_source(source, query)
