from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tg_lottery.domain.errors import UserFacingError


async def resolve_required_chat(
    bot: Bot,
    ref: int | str,
    *,
    admin_user_id: int | None = None,
) -> dict[str, Any]:
    chat = await _get_chat(bot, ref)
    member = await _get_bot_member(bot, chat)
    _validate_bot_membership(chat, member)
    if admin_user_id is not None:
        creator_member = await _get_creator_member(bot, chat, admin_user_id)
        _validate_creator_membership(chat, creator_member)
    username = chat.username
    return {
        "id": chat.id,
        "title": chat.title or chat.full_name or "未命名群组/频道",
        "type": str(chat.type),
        "username": username,
        "private": not bool(username),
        "join_url": f"https://t.me/{username}" if username else None,
        "validated": True,
        "bot_admin_validated": True,
        "creator_admin_validated": admin_user_id is not None,
        "member_can_send_messages": _member_can_send_messages(chat),
    }


async def _get_chat(bot: Bot, ref: int | str) -> Any:
    try:
        return await bot.get_chat(ref)
    except TelegramAPIError as exc:
        label = ref if isinstance(ref, str) else f"群/频道 ID {ref}"
        raise UserFacingError(
            f"无法访问参与条件 {label}；请确认链接有效，并将机器人加入该群或频道后重试"
        ) from exc


async def _get_bot_member(bot: Bot, chat: Any) -> Any:
    try:
        return await bot.get_chat_member(chat.id, bot.id)
    except TelegramAPIError as exc:
        raise UserFacingError(
            f"无法检查参与条件「{_chat_title(chat)}」的机器人权限；请将机器人设为管理员后重试"
        ) from exc


async def _get_creator_member(bot: Bot, chat: Any, user_id: int) -> Any:
    try:
        return await bot.get_chat_member(chat.id, user_id)
    except TelegramAPIError as exc:
        raise UserFacingError(
            f"无法检查参与条件「{_chat_title(chat)}」的创建者权限；请确认创建者仍是管理员后重试"
        ) from exc


def _validate_bot_membership(chat: Any, member: Any) -> None:
    status = str(member.status)
    if status not in {"administrator", "creator"}:
        raise UserFacingError(f"参与条件「{_chat_title(chat)}」：请先将机器人设为管理员")
    if not chat.username and status != "creator":
        if getattr(member, "can_invite_users", True) is False:
            raise UserFacingError(f"参与条件「{_chat_title(chat)}」：请为机器人开启邀请用户权限")


def _validate_creator_membership(chat: Any, member: Any) -> None:
    if str(member.status) not in {"administrator", "creator"}:
        raise UserFacingError(f"参与条件「{_chat_title(chat)}」：创建者必须是管理员")


def _chat_title(chat: Any) -> str:
    return chat.title or chat.full_name or "未命名群组/频道"


def _member_can_send_messages(chat: Any) -> bool | None:
    permissions = getattr(chat, "permissions", None)
    value = getattr(permissions, "can_send_messages", None)
    return value if isinstance(value, bool) else None
