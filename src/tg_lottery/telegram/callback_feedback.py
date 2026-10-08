import asyncio
from contextlib import asynccontextmanager
from time import perf_counter

from aiogram.exceptions import TelegramAPIError

ACK_TIMEOUT_SECONDS = 2


@asynccontextmanager
async def callback_feedback(query, logger, *, deferred: bool):
    if deferred:
        yield
        return
    acknowledgement = asyncio.create_task(answer_callback(query, logger))
    try:
        # Initiate feedback before the business path, without waiting for the network.
        await asyncio.sleep(0)
        yield
    except BaseException:
        await _cancel_acknowledgement(acknowledgement, logger)
        raise
    else:
        await acknowledgement


async def _cancel_acknowledgement(task, logger) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:
        # Preserve the business failure while exposing a concurrent adapter defect.
        logger.exception("callback acknowledgement cleanup failed")


async def answer_callback(query, logger) -> None:
    started = perf_counter()
    try:
        async with asyncio.timeout(ACK_TIMEOUT_SECONDS):
            await query.answer()
    except (TelegramAPIError, OSError, TimeoutError) as exc:
        logger.warning(
            "callback acknowledgement failed: %s",
            exc,
            extra={
                "operation": "answer_callback",
                "error_code": type(exc).__name__,
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "completed": False,
            },
        )
        return
    logger.info(
        "callback acknowledgement finished",
        extra={
            "operation": "answer_callback",
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "completed": True,
        },
    )
