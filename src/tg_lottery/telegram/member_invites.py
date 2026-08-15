from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def invite_link_text(campaign_name: str, link: str, *, active: bool) -> str:
    if active:
        note = "仅在本期报名截止前通过该链接加入，才会计算本期邀请加成。"
    else:
        note = "本期已截止；此链接仍可入群，但不再计算本期或以后任何抽奖加成。"
    return f"🔗 {campaign_name} 专属邀请链接\n\n{link}\n\n{note}"


def invite_link_keyboard(link: str, campaign_name: str) -> InlineKeyboardMarkup:
    share = "https://t.me/share/url?url=" + quote(link, safe="")
    share += "&text=" + quote(f"邀请你参加「{campaign_name}」抽奖", safe="")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="打开群聊", url=link)],
            [InlineKeyboardButton(text="转发给好友", url=share)],
        ]
    )
