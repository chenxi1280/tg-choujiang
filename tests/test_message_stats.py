from datetime import UTC, datetime
from functools import partial

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.activity import MessageObservation
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.infrastructure.db.uow import UnitOfWork


@pytest_asyncio.fixture
async def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> partial[UnitOfWork]:
    return partial(UnitOfWork, session_factory)


def _obs(text, *, chat_id=-100123, user_id=42, message_id=1):
    return MessageObservation(
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        event_at=datetime(2026, 8, 15, tzinfo=UTC),
        text=text,
    )


async def test_counts_valid_messages_and_dedups_consecutive(uow_factory) -> None:
    stats = MessageStatsService(uow_factory)
    await stats.record(_obs("第一条有效消息", message_id=1))
    await stats.record(_obs("第一条有效消息", message_id=2))  # 连续重复不计
    await stats.record(_obs("第二条有效消息", message_id=3))
    await stats.record(_obs("短", message_id=4))  # 短文字也计数
    await stats.record(_obs("/getid", message_id=5))  # 命令不计
    totals = await stats.totals(42, (-100123,))
    assert totals == {-100123: 3}


async def test_totals_missing_chat_returns_zero(uow_factory) -> None:
    stats = MessageStatsService(uow_factory)
    assert await stats.totals(42, (-100999,)) == {}
