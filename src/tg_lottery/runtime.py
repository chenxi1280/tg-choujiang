import asyncio
import logging
import os
import signal
import socket
from uuid import uuid4

from aiogram import Bot


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


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
