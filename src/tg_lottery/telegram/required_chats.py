import asyncio
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tg_lottery.domain.errors import UserFacingError


async def resolve_required_chat(
    bot: Bot,
    ref: int | str,
    *,
    admin_user_id: int | None = None,
    require_invites: bool = False,
) -> dict[str, Any]:
    chat, member, creator_member = await _chat_and_members(bot, ref, admin_user_id=admin_user_id)
    if str(chat.type) not in {"group", "supergroup", "channel"}:
        raise UserFacingError("准入条件只能选择群聊或频道")
    if admin_user_id is not None:
        _validate_creator_membership(chat, creator_member)
    _validate_bot_membership(chat, member)
    if require_invites and not bool(getattr(member, "can_invite_users", False)):
        raise UserFacingError(f"参与条件「{_chat_title(chat)}」：请为机器人开启邀请权限后重试")
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


async def _chat_and_members(bot: Bot, ref: int | str, *, admin_user_id: int | None):
    if isinstance(ref, int):
        requests = [_get_chat(bot, ref), bot.get_chat_member(ref, bot.id)]
        if admin_user_id is not None:
            requests.append(bot.get_chat_member(ref, admin_user_id))
        values = await asyncio.gather(*requests, return_exceptions=True)
        chat = values[0]
        if isinstance(chat, BaseException):
            raise chat
        member = _member_result(chat, values[1], role="机器人")
        creator = (
            _member_result(chat, values[2], role="创建者") if admin_user_id is not None else None
        )
        return chat, member, creator
    chat = await _get_chat(bot, ref)
    if admin_user_id is None:
        return chat, await _get_bot_member(bot, chat), None
    member, creator = await asyncio.gather(
        _get_bot_member(bot, chat), _get_creator_member(bot, chat, admin_user_id)
    )
    return chat, member, creator


def _member_result(chat: Any, result: Any, *, role: str):
    if isinstance(result, TelegramAPIError):
        raise UserFacingError(
            f"无法检查参与条件「{_chat_title(chat)}」的{role}权限；请确认管理员权限后重试"
        ) from result
    if isinstance(result, BaseException):
        raise result
    return result


async def _get_chat(bot: Bot, ref: int | str) -> Any:
    try:
        return await bot.get_chat(ref)
    except TelegramAPIError as exc:
        label = ref if isinstance(ref, str) and not ref.lstrip("-").isdigit() else "所选群/频道"
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
