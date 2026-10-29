from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions, ReplyParameters

from tg_lottery.telegram.remote_serialization import remote_object


async def deliver_query(bot, outbox):
    from tg_lottery.telegram.gateway import GatewayResult

    payload = outbox.payload
    options = dict(
        parse_mode="HTML",
        message_thread_id=payload.get("thread_id"),
        reply_markup=InlineKeyboardMarkup.model_validate(payload["reply_markup"]),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    try:
        message = await bot.send_message(
            outbox.recipient_chat_id,
            payload["text"],
            reply_parameters=ReplyParameters(message_id=payload["request_id"]),
            **options,
        )
    except TelegramBadRequest as exc:
        if "message to be replied not found" not in str(exc).lower():
            raise
        import logging

        logging.getLogger(__name__).warning("query request disappeared before reply", exc_info=True)
        message = await bot.send_message(outbox.recipient_chat_id, payload["text"], **options)
    return GatewayResult(remote_result=remote_object(message), remote_message_id=message.message_id)
