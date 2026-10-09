"""Treat Telegram's explicit unchanged-panel response as an idempotent edit."""

from aiogram.exceptions import TelegramBadRequest


async def edit_panel(message, text: str, **options) -> None:
    try:
        await message.edit_text(text, **options)
    except TelegramBadRequest as exc:
        if not exc.message.startswith("Bad Request: message is not modified:"):
            raise
