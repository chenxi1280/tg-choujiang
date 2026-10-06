from aiogram.exceptions import TelegramBadRequest


async def answer_callback(query, logger) -> None:
    try:
        await query.answer()
    except TelegramBadRequest as exc:
        logger.warning(
            "callback acknowledgement rejected by Telegram",
            extra={"callback_query_id": query.id, "error": str(exc)},
        )
