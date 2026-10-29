from tg_lottery.telegram.remote_serialization import remote_object


async def deliver_start(bot, outbox):
    from tg_lottery.telegram.gateway import GatewayResult, deliver_campaign_card

    if outbox.operation == "send_start_rules":
        message = await bot.send_message(
            outbox.recipient_chat_id,
            outbox.payload["text"],
            parse_mode="HTML",
            message_thread_id=outbox.payload.get("message_thread_id"),
        )
    else:
        message = await deliver_campaign_card(bot, outbox.recipient_chat_id, outbox.payload)
    return GatewayResult(remote_result=remote_object(message), remote_message_id=message.message_id)
