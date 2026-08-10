import re
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import User

from tg_lottery.domain.errors import UserFacingError


def creator_issuer(user: User) -> dict[str, Any]:
    return {
        "issuer": "",
        "issuer_user_id": user.id,
        "issuer_username_at_publish": user.username,
        "issuer_display_name_at_publish": user.full_name,
        "issuer_name": user.full_name,
        "issuer_source": "creator",
    }


async def resolve_issuer(
    bot: Bot,
    value: str,
    creator: User,
    *,
    candidate_user_ids: tuple[int, ...] = (),
) -> dict[str, Any]:
    reference = value.strip()
    if not reference:
        return creator_issuer(creator)
    if _is_username(reference):
        return await _resolve_username(bot, reference, candidate_user_ids)
    chat_ref = _issuer_user_id(reference)
    try:
        chat = await bot.get_chat(chat_ref)
    except TelegramAPIError as exc:
        raise UserFacingError("无法解析发奖人员，请确认用户 ID 或用户名后重试") from exc
    if str(chat.type) != "private":
        raise UserFacingError("发奖人员必须是 Telegram 用户")
    return _issuer_record(reference, chat)


async def _resolve_username(
    bot: Bot, reference: str, candidate_user_ids: tuple[int, ...]
) -> dict[str, Any]:
    expected = reference.removeprefix("@").lower()
    last_error: TelegramAPIError | None = None
    for user_id in candidate_user_ids:
        try:
            chat = await bot.get_chat(user_id)
        except TelegramAPIError as exc:
            last_error = exc
            continue
        if str(chat.type) == "private" and str(chat.username or "").lower() == expected:
            return _issuer_record(reference, chat)
    message = "无法解析该用户名；该用户需先参与过活动，或请改用 Telegram 用户 ID"
    raise UserFacingError(message) from last_error


def _issuer_record(reference: str, chat: Any) -> dict[str, Any]:
    if str(chat.type) != "private":
        raise UserFacingError("发奖人员必须是 Telegram 用户")
    display_name = chat.full_name or chat.title
    if not display_name:
        raise UserFacingError("无法读取发奖人员名称")
    return {
        "issuer": reference,
        "issuer_user_id": int(chat.id),
        "issuer_username_at_publish": chat.username,
        "issuer_display_name_at_publish": str(display_name),
        "issuer_name": str(display_name),
        "issuer_source": "custom",
    }


def _is_username(value: str) -> bool:
    return re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value) is not None


def _issuer_user_id(value: str) -> int:
    try:
        user_id = int(value)
    except ValueError as exc:
        raise UserFacingError("发奖人员需填写 Telegram 用户 ID 或 @username") from exc
    if user_id <= 0:
        raise UserFacingError("发奖人员必须是 Telegram 用户")
    return user_id
