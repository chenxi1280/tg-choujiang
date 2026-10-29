from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from tg_lottery.presentation.featured_channel import featured_content
from tg_lottery.telegram.gateway_edit import confirmed_edit
from tg_lottery.telegram.remote_serialization import remote_object


async def deliver_featured(bot, request):
    from tg_lottery.telegram.gateway import GatewayResult

    if request.recipient_chat_id >= 0:
        raise ValueError("featured messages require a channel recipient")
    text, buttons = featured_content(
        request.payload, digest=request.operation == "send_featured_digest"
    )
    options = dict(
        chat_id=request.recipient_chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(**button)] for button in buttons]
        ),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    if request.operation == "edit_featured_campaign":
        message_id = int(request.payload["message_id"])
        return await confirmed_edit(
            bot.edit_message_text(message_id=message_id, **options), message_id=message_id
        )
    message = await bot.send_message(**options, disable_notification=False)
    return GatewayResult(remote_result=remote_object(message), remote_message_id=message.message_id)
