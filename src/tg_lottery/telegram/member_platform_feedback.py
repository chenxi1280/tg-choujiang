from aiogram.types import InlineKeyboardButton

from tg_lottery.presentation.condition_progress import bot_condition_label
from tg_lottery.telegram.ui_style import row_keyboard


async def platform_feedback(flow, campaign, *, user_id, text, keyboard):
    if not campaign.rules.get("platform_policy"):
        return text, keyboard
    if flow._relationships is None:
        raise RuntimeError("platform conditions require bot relationship evidence")
    relation = await flow._relationships.observation(user_id)
    text = bot_condition_label(relation["observed_status"]) + "\n" + text
    rows = list(keyboard.inline_keyboard)
    if relation["state"] != "eligible":
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text="打开本场并启动机器人",
                    url=f"https://t.me/{flow._bot_username}?start=join_{campaign.public_ref}",
                )
            ],
        )
    return text, row_keyboard(rows)
