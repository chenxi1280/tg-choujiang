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
    operation_task = asyncio.ensure_future(operation)
    heartbeat_task = asyncio.create_task(
        _heartbeat_until_stopped(heartbeat, interval_seconds, stop)
    )
    operation_finished = False
    try:
        done, _ = await asyncio.wait(
            {operation_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        operation_finished = operation_task in done
        if operation_finished:
            return operation_task.result()
        heartbeat_task.result()
        raise RuntimeError("heartbeat stopped before operation completed")
    finally:
        stop.set()
        if operation_finished:
            await _finish_heartbeat(heartbeat_task)
        else:
            await _cancel_if_pending(operation_task)


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


async def _finish_heartbeat(task: asyncio.Task[None]) -> None:
    if not task.done():
        await task
        return
    try:
        task.result()
    except (Exception, asyncio.CancelledError):
        return


async def _cancel_if_pending(task: asyncio.Future[object]) -> None:
    if task.done():
        return
    task.cancel()
    try:
        await task
    except (Exception, asyncio.CancelledError):
        return
