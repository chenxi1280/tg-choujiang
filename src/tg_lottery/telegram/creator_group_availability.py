from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError


@dataclass(frozen=True)
class GroupAvailability:
    selectable: bool
    status: str
    reason: str


async def inspect_group_availability(bot: Bot, chat_id: int, user_id: int) -> GroupAvailability:
    try:
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        creator = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return GroupAvailability(
            False,
            "核验失败",
            "Telegram 暂时无法核验该群。请确认机器人仍在群内，然后点击重新检测。",
        )
    if str(creator.status) not in {"administrator", "creator"}:
        return GroupAvailability(
            False,
            "你已不是管理员",
            "请先恢复你的群管理员身份，再回到这里重新检测。",
        )
    bot_status = str(bot_member.status)
    if bot_status in {"left", "kicked"}:
        return GroupAvailability(
            False,
            "机器人不在群内",
            "请把机器人重新加入该群并设为管理员，然后点击重新检测。",
        )
    if bot_status not in {"administrator", "creator"}:
        return GroupAvailability(
            False,
            "机器人不是管理员",
            "请在群设置中把机器人提升为管理员，然后点击重新检测。",
        )
    return await _chat_type_availability(bot, chat_id)


async def _chat_type_availability(bot: Bot, chat_id: int) -> GroupAvailability:
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramAPIError:
        return GroupAvailability(False, "核验失败", "无法核验目标类型，请点击重新检测。")
    if str(chat.type) not in {"group", "supergroup"}:
        return GroupAvailability(
            False, "频道请设为准入条件", "频道不能作为参与活动的群，请在“准入条件”中添加该频道。"
        )
    return GroupAvailability(True, "可选择", "")
