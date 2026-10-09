import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from time import perf_counter

from aiogram.exceptions import TelegramAPIError

ACK_TIMEOUT_SECONDS = 2
_ACTIVE_FEEDBACK = ContextVar("active_callback_feedback", default=None)


@asynccontextmanager
async def callback_feedback(query, logger, *, deferred: bool):
    if deferred:
        yield
        return
    acknowledgement = asyncio.create_task(answer_callback(query, logger))
    token = _ACTIVE_FEEDBACK.set((_query_key(query), acknowledgement))
    try:
        # Initiate feedback before the business path, without waiting for the network.
        await asyncio.sleep(0)
        yield
    except BaseException:
        await _cancel_acknowledgement(acknowledgement, logger)
        raise
    else:
        await acknowledgement
    finally:
        _ACTIVE_FEEDBACK.reset(token)


async def finish_callback_feedback(query) -> None:
    active = _ACTIVE_FEEDBACK.get()
    if active is not None and active[0] == _query_key(query):
        # A late blank acknowledgement must never replace the actual result alert.
        await active[1]


def _query_key(query):
    return getattr(query, "id", id(query))


async def _cancel_acknowledgement(task, logger) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:
        # Preserve the business failure while exposing a concurrent adapter defect.
        logger.exception("callback acknowledgement cleanup failed")


async def answer_callback(
    query, logger, *, text: str | None = None, alert: bool | None = None
) -> None:
    started = perf_counter()
    result = text is not None or alert is not None
    try:
        async with asyncio.timeout(ACK_TIMEOUT_SECONDS):
            if not result:
                await query.answer()
            else:
                await query.answer(text[:200] if text is not None else None, show_alert=bool(alert))
    except (TelegramAPIError, OSError, TimeoutError) as exc:
        logger.warning(
            "callback acknowledgement failed: %s"
            if not result
            else "callback result acknowledgement rejected by Telegram: %s",
            exc,
            extra={
                "operation": "callback_result" if result else "answer_callback",
                "error_code": type(exc).__name__,
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "completed": False,
            },
        )
        return
    logger.info(
        "callback acknowledgement finished",
        extra={
            "operation": "callback_result" if result else "answer_callback",
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "completed": True,
        },
    )
