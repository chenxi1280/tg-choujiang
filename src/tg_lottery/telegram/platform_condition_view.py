from aiogram.types import InlineKeyboardButton

from tg_lottery.presentation.condition_progress import bot_condition_label


async def platform_condition_lines(relationships, *, campaign, user_id, bot_username):
    if not campaign.rules.get("platform_policy"):
        return [], []
    if relationships is None:
        raise RuntimeError("platform condition view requires durable relationship evidence")
    relation = await relationships.observation(user_id)
    lines = [bot_condition_label(relation["observed_status"])]
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
