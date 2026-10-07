from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


async def platform_feedback(flow, campaign, *, user_id, text, keyboard):
    if not campaign.rules.get("platform_policy"):
        return text, keyboard
    if flow._relationships is None:
        raise RuntimeError("platform conditions require bot relationship evidence")
    relation = await flow._relationships.observation(user_id)
    labels = {
        "bot_not_started": "尚未启动本机器人，请打开下方本场入口并点击开始。",
        "bot_blocked": "机器人已被屏蔽，请解除屏蔽并从本场入口发送/start。",
        "bot_relationship_unknown": "机器人关系证据待核验，请从本场入口重新发送/start。",
        "bot_started": "本机器人已启动；其他条件仍须全部满足。",
    }
    text = labels[relation["observed_status"]] + "\n" + text
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
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
