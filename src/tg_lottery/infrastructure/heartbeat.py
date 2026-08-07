import asyncio
from collections.abc import Awaitable, Callable


async def run_with_heartbeat[Result](
    operation: Awaitable[Result],
    heartbeat: Callable[[], Awaitable[None]],
    interval_seconds: float,
) -> Result:
    if interval_seconds <= 0:
        raise ValueError("heartbeat interval must be positive")
    stop = asyncio.Event()
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_heartbeat_until_stopped(heartbeat, interval_seconds, stop))
        try:
            return await operation
        finally:
            stop.set()


async def _heartbeat_until_stopped(
    heartbeat: Callable[[], Awaitable[None]],
    interval_seconds: float,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            await heartbeat()
