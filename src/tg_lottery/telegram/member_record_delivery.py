from aiogram import Bot
from aiogram.types import Chat, Message, User

from tg_lottery.domain.errors import UserFacingError


def private_record_message(
    message: Message,
    bot: Bot,
    *,
    user_id: int,
    user: User | None = None,
) -> Message:
    if message.chat.type == "private" and message.chat.id != user_id:
        raise UserFacingError("只能在本人私聊中查看参与记录")
    fields: dict[str, object] = {
        "chat": Chat(id=user_id, type="private"),
        "message_thread_id": None,
        "is_topic_message": False,
        "business_connection_id": None,
        "direct_messages_topic": None,
    }
    if user is not None:
        fields["from_user"] = user
    return message.model_copy(update=fields).as_(bot)
