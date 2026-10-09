import asyncio

import pytest

from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import BotRuntimeLease
from tg_lottery.telegram.polling import TelegramPollingAdapter


class BlockingPollingBot:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.active_requests = 0

    async def get_updates(self, **kwargs):
        self.active_requests += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.active_requests -= 1


def _adapter(bot, session_factory, owner):
    return TelegramPollingAdapter(
        bot=bot,
        bot_id=123,
        owner_id=owner,
        session_factory=session_factory,
        timeout_seconds=30,
        lease_seconds=120,
    )


@pytest.mark.parametrize("shutdown", ["stop", "cancel"])
async def test_stop_reaps_poll_before_releasing_lease_and_preserves_fence(
    session_factory, shutdown
):
    bot = BlockingPollingBot()
    old = _adapter(bot, session_factory, "old")
    stop = asyncio.Event()
    task = asyncio.create_task(old.run_forever(stop))
    await asyncio.wait_for(bot.started.wait(), 1)
    successor = _adapter(bot, session_factory, "successor")
    assert not await successor._acquire_or_renew_lease()
    if shutdown == "stop":
        stop.set()
        await asyncio.wait_for(task, 1)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert bot.cancelled.is_set() and bot.active_requests == 0
    async with session_factory() as session:
        released = await session.get(BotRuntimeLease, 123)
        assert released.lease_until <= utcnow() and released.fence == 1
    assert await successor._acquire_or_renew_lease()
    async with session_factory() as session:
        acquired = await session.get(BotRuntimeLease, 123)
        assert acquired.owner_id == "successor" and acquired.fence == 2
        expires_at = acquired.lease_until
    await old._release_lease()
    async with session_factory() as session:
        protected = await session.get(BotRuntimeLease, 123)
        assert protected.owner_id == "successor" and protected.lease_until == expires_at
