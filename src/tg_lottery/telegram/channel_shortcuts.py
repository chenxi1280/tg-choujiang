"""Personal overlay compatibility adapter; never edits the shared announcement."""

import logging
from uuid import UUID

from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardButton

from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import TelegramOutbox
from tg_lottery.telegram.member_condition_check import condition_check_payload
from tg_lottery.telegram.remote_serialization import remote_object
from tg_lottery.telegram.ui_style import row_keyboard


class SendPersonalMessage(SendMessage):
    # Bot API parameter introduced after the currently pinned aiogram schema.
    ephemeral_message_parameters: dict[str, object]


async def deliver_shortcut(bot, outbox):
    from tg_lottery.telegram.gateway import GatewayResult

    payload = outbox.payload
    message = await bot.send_message(
        outbox.recipient_chat_id,
        "🎟️ 查看本场活动和本人条件，报名状态以机器人页面为准。",
        reply_markup=row_keyboard(
            [
                [
                    InlineKeyboardButton(
                        text="查看我的条件", callback_data=f"cp:{payload['shortcut_id']}:0"
                    )
                ],
                [InlineKeyboardButton(text="🤖 进入机器人", url=_private_url(payload))],
            ]
        ),
    )
    return GatewayResult(remote_result=remote_object(message), remote_message_id=message.message_id)


async def handle_shortcut(flow, query):
    if not (query.data or "").startswith("cp:"):
        return False
    try:
        _, key, page = query.data.split(":")
        identifier, page = UUID(hex=key), max(0, int(page))
    except (ValueError, TypeError):
        await query.answer("入口已失效，请从原频道或机器人重新查看。", show_alert=True)
        return True
    async with flow._queries._session_factory() as session:
        source = await session.get(TelegramOutbox, identifier)
    if not _valid_source(source, query):
        await query.answer("请从原频道入口或机器人查看本场活动。", show_alert=True)
        return True
    campaign = await flow._queries.campaign_by_reference(source.payload["campaign_ref"])
    if campaign is None:
        await query.answer("活动已不存在，请进入机器人查看其他活动。", show_alert=True)
        return True
    text, keyboard = await condition_check_payload(
        query,
        campaign=campaign,
        queries=flow._queries,
        bot=flow._bot,
        profiles=flow._profiles,
        cipher=flow._cipher,
        relationships=flow._relationships,
        bot_username=flow._bot_username,
        page=page,
    )
    keyboard = _personal_keyboard(keyboard, source=source)
    try:
        await _send_personal(flow._bot, query, text=text, keyboard=keyboard)
    except TelegramAPIError:
        logging.getLogger(__name__).exception("channel personal response failed")
        await query.answer("原地个人页面暂不可用，请点下方进入机器人查看本场。", show_alert=True)
    return True


def _valid_source(source, query):
    if (
        not source
        or source.operation != "send_channel_shortcut"
        or source.state != OutboxState.CONFIRMED
    ):
        return False
    message = query.message
    if source.recipient_chat_id != message.chat.id:
        return False
    if message.ephemeral_message_id:
        return message.receiver_user is not None and message.receiver_user.id == query.from_user.id
    return source.remote_message_id == message.message_id


def _personal_keyboard(keyboard, *, source):
    rows = []
    for row in keyboard.inline_keyboard:
        selected = [mapped for button in row if (mapped := _personal_button(button, source))]
        if selected:
            rows.append(selected)
    rows.append([InlineKeyboardButton(text="🔄 重新核验", callback_data=f"cp:{source.id.hex}:0")])
    rows.append(
        [InlineKeyboardButton(text="🤖 进入机器人查看 / 报名", url=_private_url(source.payload))]
    )
    return row_keyboard(rows)


async def _send_personal(bot, query, *, text, keyboard):
    message = query.message
    if message.ephemeral_message_id:
        await bot.edit_ephemeral_message_text(
            chat_id=message.chat.id,
            ephemeral_message_id=message.ephemeral_message_id,
            receiver_user_id=query.from_user.id,
            text=text,
            reply_markup=keyboard,
        )
    else:
        await bot(
            SendPersonalMessage(
                chat_id=message.chat.id,
                text=text,
                reply_markup=keyboard,
                ephemeral_message_parameters={
                    "receiver_user_id": query.from_user.id,
                    "callback_query_id": query.id,
                },
            )
        )


def _private_url(payload):
    return f"https://t.me/{payload['bot_username']}?start=join_{payload['campaign_ref']}"


def _personal_button(button, source):
    if button.url:
        return button
    if (button.callback_data or "").startswith("m:conditions:"):
        page = button.callback_data.rsplit(":", 1)[-1]
        return button.model_copy(update={"callback_data": f"cp:{source.id.hex}:{page}"})
    return None
