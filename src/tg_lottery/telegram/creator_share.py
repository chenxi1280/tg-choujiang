from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def publication_receipt(campaign, bot_username: str) -> tuple[str, InlineKeyboardMarkup]:
    link = f"https://t.me/{bot_username}?start=join_{campaign.public_ref}"
    text = (
        f"🎰 {campaign.name}\n\n"
        "🎉 发布请求已提交。全部参与群活动卡确认送达后活动才正式开始；"
        "可在「抽奖管理」查看投递状态。\n\n"
        f"🎫 点击链接参与抽奖：\n{link}\n\n"
        "长按本消息即可转发；转发后点击正文中的链接参与。"
    )
    share = "https://t.me/share/url?url=" + quote(link, safe="")
    share += "&text=" + quote(f"邀请你参加「{campaign.name}」抽奖", safe="")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎫 立即参与", url=link)],
            [InlineKeyboardButton(text="转发参与链接", url=share)],
        ]
    )
    return text, keyboard
