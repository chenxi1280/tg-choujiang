from aiogram.types import InlineKeyboardButton


async def platform_condition_lines(relationships, *, campaign, user_id, bot_username):
    if not campaign.rules.get("platform_policy"):
        return [], []
    if relationships is None:
        raise RuntimeError("platform condition view requires durable relationship evidence")
    relation = await relationships.observation(user_id)
    labels = {
        "bot_started": "已启动，未发现有效屏蔽事实",
        "bot_not_started": "尚未记录有效/start，请从本场入口启动",
        "bot_blocked": "已屏蔽，请解除屏蔽并发送/start",
        "bot_relationship_unknown": "证据冲突或未知，请重新发送/start核验",
    }
    lines = ["平台必备条件 · 启动本机器人：" + labels[relation["observed_status"]]]
    rows = []
    if relation["state"] != "eligible":
        rows.append(
            [
                InlineKeyboardButton(
                    text="启动本机器人（本场入口）",
                    url=f"https://t.me/{bot_username}?start=join_{campaign.public_ref}",
                )
            ]
        )
    return lines, rows
