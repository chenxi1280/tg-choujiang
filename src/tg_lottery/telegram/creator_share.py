from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.telegram.ui_style import style_keyboard


def publication_receipt(campaign, bot_username: str) -> tuple[str, InlineKeyboardMarkup]:
    link = f"https://t.me/{bot_username}?start=join_{campaign.public_ref}"
    text = (
        f"✨ {campaign.name}\n\n"
        "🎉 发布请求已提交。全部参与群活动卡确认送达后活动才正式开始；"
        "可在「抽奖管理」查看投递状态。\n\n"
        f"🤝 点击链接参与抽奖：\n{link}\n\n"
        "长按本消息可转发链接；「分享活动卡片」提供包含封面、条件和奖品的同款公告。"
    )
    share = f"https://t.me/{bot_username}?start=share_{campaign.public_ref}"
    keyboard = style_keyboard(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="查看本场投递状态", callback_data=f"nav:campaign:{campaign.public_ref}"
                    )
                ],
                [InlineKeyboardButton(text="查看参与条件", url=link)],
                [InlineKeyboardButton(text="分享活动卡片", url=share)],
                [InlineKeyboardButton(text="我发起的抽奖", callback_data="nav:created")],
            ]
        )
    )
    return text, keyboard
