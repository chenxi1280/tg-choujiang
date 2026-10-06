from typing import Any

from aiogram.types import CallbackQuery, Message, User

from tg_lottery.telegram.creator_renderer import PanelTarget


def callback_message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message


def message_user(message: Message) -> User:
    if message.from_user is None:
        raise ValueError("无法识别当前用户")
    return message.from_user


def panel_target(message: Message, data: dict[str, Any]) -> PanelTarget:
    return PanelTarget(
        user_id=message_user(message).id,
        chat_id=message.chat.id,
        message_id=int(data["panel_message_id"]),
    )
