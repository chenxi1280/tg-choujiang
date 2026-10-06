from aiogram.types import Message

from tg_lottery.application.activity import MessageObservation


def observe_message(message: Message) -> MessageObservation:
    media_key = _media_key(message)
    return MessageObservation(
        chat_id=message.chat.id,
        message_id=message.message_id,
        user_id=message.from_user.id,
        event_at=message.date,
        text=message.text or message.caption,
        is_bot=message.from_user.is_bot,
        is_service=message.text is None and message.caption is None and media_key is None,
        is_forwarded=message.forward_origin is not None,
        media_key=media_key,
    )


def _media_key(message: Message) -> str | None:
    if message.photo:
        return f"photo:{message.photo[-1].file_unique_id}"
    if message.sticker:
        return f"sticker:{message.sticker.file_unique_id}"
    if message.animation:
        return f"animation:{message.animation.file_unique_id}"
    return None
