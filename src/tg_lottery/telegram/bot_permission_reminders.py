from __future__ import annotations

from aiogram import Bot
from aiogram.types import ChatMemberUpdated


async def send_bot_permission_reminder(bot: Bot, event: ChatMemberUpdated) -> None:
    channel = str(event.chat.type) == "channel"
    scope = "频道" if channel else "群"
    old_status = str(event.old_chat_member.status)
    new_status = str(event.new_chat_member.status)
    if new_status in {"member", "restricted"} and old_status != new_status:
        await bot.send_message(
            event.chat.id,
            "⚠️ 抽奖机器人已加入，但还不是管理员。请把机器人设为管理员并开启邀请用户权限；"
            f"完成后回到创建页点击「重新检测」，否则该{scope}不能作为抽奖条件。",
        )
        return
    if new_status not in {"administrator", "creator"}:
        return
    if old_status not in {"administrator", "creator"}:
        purpose = (
            "请在机器人私聊的「准入条件」中添加本频道。私有频道还需开启邀请用户权限。"
            if channel
            else "现在可以在机器人私聊中创建抽奖并选择本群；"
            "邀请加成还需要开启邀请用户权限，活跃加成（人品）需要允许普通成员发送文本。"
        )
        await bot.send_message(
            event.chat.id,
            "✅ 抽奖机器人管理员权限已生效。" + purpose,
        )
        return
    if _invite_permission_removed(event):
        affected = "私有频道的加入入口" if channel else "本群的个人邀请链接与邀请加成"
        await bot.send_message(
            event.chat.id,
            f"⚠️ 机器人的邀请用户权限已被关闭。{affected}将无法工作；请恢复该权限后重新检测。",
        )


def _invite_permission_removed(event: ChatMemberUpdated) -> bool:
    old_value = getattr(event.old_chat_member, "can_invite_users", None)
    new_value = getattr(event.new_chat_member, "can_invite_users", None)
    return old_value is True and new_value is False
