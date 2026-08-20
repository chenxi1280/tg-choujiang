from datetime import datetime
from uuid import UUID

from aiogram.types import Message, User

from tg_lottery.application.participants import RegistrationRequest


def required_user(message: Message) -> User:
    if message.from_user is None:
        raise ValueError("Telegram message has no sender")
    return message.from_user


def registration_request(
    campaign_id: UUID,
    user: User,
    observed_at: datetime,
    *,
    source_chat_id: int | None = None,
) -> RegistrationRequest:
    return RegistrationRequest(
        campaign_id=campaign_id,
        user_id=user.id,
        username=user.username,
        display_name=user.full_name,
        observed_at=observed_at,
        source_chat_id=source_chat_id,
    )


def registration_request_from_message(
    campaign_id: UUID,
    user: User,
    message: Message,
    *,
    observed_at: datetime | None,
) -> RegistrationRequest:
    return registration_request(
        campaign_id,
        user,
        observed_at or message.date,
        source_chat_id=_source_chat_id(message),
    )


def _source_chat_id(message: Message) -> int | None:
    chat = getattr(message, "chat", None)
    if chat is None or getattr(chat, "type", None) == "private":
        return None
    return int(chat.id)
