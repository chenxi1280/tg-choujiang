from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tg_lottery.domain.errors import UserFacingError


async def resolve_required_chat(bot: Bot, ref: int | str) -> dict[str, Any]:
    try:
        chat = await bot.get_chat(ref)
        member = await bot.get_chat_member(chat.id, bot.id)
    except TelegramAPIError as exc:
        raise UserFacingError("无法验证其中一个参与条件，请确认机器人权限后重试") from exc
    status = str(member.status)
    if status not in {"administrator", "creator"}:
        raise UserFacingError("机器人必须是每个参与条件群/频道的管理员")
    if status != "creator" and getattr(member, "can_invite_users", True) is False:
        raise UserFacingError("机器人必须能为私有参与条件创建邀请链接")
    username = chat.username
    return {
        "id": chat.id,
        "title": chat.title or chat.full_name or "未命名群组/频道",
        "type": str(chat.type),
        "username": username,
        "private": not bool(username),
        "join_url": f"https://t.me/{username}" if username else None,
        "validated": True,
    }
