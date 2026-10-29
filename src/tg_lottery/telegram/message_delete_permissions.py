"""Read-only per-target checks; own Bot messages and user commands have different rights."""

import logging

from aiogram.exceptions import TelegramAPIError

from tg_lottery.application.records import utcnow
from tg_lottery.telegram.creator_group_selection import selected_chat_ids
from tg_lottery.telegram.public_projection import format_public_time


async def inspect_deletion_permissions(bot, data):
    targets = _targets(data)
    results = []
    for chat_id, target in targets.items():
        try:
            chat = await bot.get_chat(chat_id)
            member = await bot.get_chat_member(chat_id, bot.id)
            own, commands = _rights(member, str(chat.type))
        except TelegramAPIError:
            logging.getLogger(__name__).warning(
                "message deletion permission check failed",
                extra={"chat_id": chat_id},
                exc_info=True,
            )
            own, commands = None, None
        results.append(
            {
                "chat_id": chat_id,
                "title": target.get("title") or str(chat_id),
                "own": own,
                "commands": commands,
            }
        )
    return {
        "delete_permission_checks": results,
        "delete_permission_checked_at": utcnow().isoformat(),
    }


def _rights(member, kind):
    status = str(member.status)
    if status == "creator":
        return True, True
    if status in {"left", "kicked"}:
        return False, False
    if status == "restricted" and not member.is_member:
        return False, False
    if status not in {"member", "administrator", "restricted"}:
        return None, None
    if kind == "channel":
        can_post = getattr(member, "can_post_messages", None)
        can_delete = getattr(member, "can_delete_messages", None)
        own = True if can_post is True or can_delete is True else can_post
        return own, can_delete
    commands = (
        status == "administrator"
        if kind == "group"
        else getattr(member, "can_delete_messages", False)
    )
    return True, commands


def deletion_permission_text(data):
    checks = data.get("delete_permission_checks")
    if checks is None:
        return "\n\n🛂 删除权限：打开消息设置时按参与目标检查；实际失败仍会记录。"
    if not checks:
        return "\n\n🛂 尚未选择参与群，选择后重新打开消息设置检查删除权限。"
    lines = ["🛂 各群删除权限（本次检查，不保证之后权限不变）"]
    for row in checks:
        lines.append(f"{row['title']}（{row['chat_id']}）：" + _status(row["own"], own=True))
        if data.get("delete_trigger_message"):
            lines.append("└ " + _status(row["commands"], own=False))
    lines.append(
        "检查时间：" + format_public_time(data["delete_permission_checked_at"], "Asia/Shanghai")
    )
    return "\n\n" + "\n".join(lines)


def _status(value, *, own):
    label = "机器人短提示" if own else "用户报名命令"
    if value is None:
        return f"⏳ {label}删除权限暂时无法确认，请稍后重新检查"
    if value:
        return f"✅ 可以删除{label}"
    return f"⚠️ 无法删除{label}，请检查机器人身份或管理员删除权限"


def _targets(data):
    records = data.get("participation_groups") or [data.get("main_chat") or {}]
    targets = {int(row["id"]): row for row in records if row.get("id")}
    for chat_id in selected_chat_ids(data):
        targets.setdefault(
            chat_id,
            {
                "id": chat_id,
                "title": data.get("group_title")
                if chat_id == data.get("chat_id")
                else str(chat_id),
            },
        )
    return targets
