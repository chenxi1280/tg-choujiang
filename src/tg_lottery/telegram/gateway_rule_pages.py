from aiogram.types import LinkPreviewOptions

from tg_lottery.telegram.gateway_edit import confirmed_edit
from tg_lottery.telegram.remote_serialization import remote_object


async def deliver_rule_page(bot, outbox):
    from tg_lottery.telegram.gateway import GatewayResult

    if outbox.operation == "send_campaign_rules":
        result = await bot.send_message(
            outbox.recipient_chat_id,
            outbox.payload["text"],
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    else:
        operation = bot.edit_message_text(
            outbox.payload["text"],
            chat_id=outbox.recipient_chat_id,
            message_id=int(outbox.payload["message_id"]),
            parse_mode="HTML",
            reply_markup=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return await confirmed_edit(operation, message_id=int(outbox.payload["message_id"]))
    return GatewayResult(
        remote_result=remote_object(result),
        remote_message_id=getattr(result, "message_id", outbox.payload.get("message_id")),
    )
