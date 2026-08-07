import asyncio
import logging
import os
import signal
import socket
from uuid import uuid4

from aiogram import Bot

from tg_lottery.application.request_context import current_update

_CONTEXT_FIELDS = (
    "bot_id",
    "update_id",
    "campaign_id",
    "job_id",
    "job_type",
    "outbox_id",
    "operation",
    "attempt_number",
    "error_code",
    "certainty",
    "first_update_id",
    "batch_size",
)


class ContextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = current_update()
        values = self._values(record, context)
        suffix = " ".join(f"{key}={value}" for key, value in values.items())
        return f"{base} {suffix}" if suffix else base

    def _values(self, record: logging.LogRecord, context: object) -> dict[str, object]:
        values = {
            key: getattr(record, key)
            for key in _CONTEXT_FIELDS
            if getattr(record, key, None) is not None
        }
        if context is not None:
            values.setdefault("bot_id", context.bot_id)
            values.setdefault("update_id", context.update_id)
        values["release_sha"] = os.getenv("RELEASE_SHA", "unknown")
        values["release_id"] = os.getenv("RELEASE_ID", "unknown")
        return values


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ContextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper()))


def owner_id(role: str) -> str:
    return f"{role}:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def stop_event() -> asyncio.Event:
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, event.set)
    return event


async def validate_bot(bot: Bot, expected_id: int) -> str:
    me = await bot.get_me()
    if me.id != expected_id:
        raise RuntimeError(f"BOT_ID {expected_id} does not match token bot id {me.id}")
    if not me.username:
        raise RuntimeError("Telegram bot has no username for deep links")
    return me.username
