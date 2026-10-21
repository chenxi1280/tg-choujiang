from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, SwitchInlineQueryChosenChat

from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.campaign_card import campaign_link
from tg_lottery.telegram.ui_style import style_keyboard


def share_to_group_button(reference):
    return InlineKeyboardButton(
        text="分享到群",
        switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
            query=f"share_{reference}",
            allow_group_chats=True,
            allow_user_chats=False,
            allow_bot_chats=False,
            allow_channel_chats=False,
        ),
    )


def campaign_share_keyboard(payload):
    if payload.get("share_closed"):
        return style_keyboard(
            InlineKeyboardMarkup(inline_keyboard=[[_link(payload, "查看活动状态与规则", "rules")]])
        )
    rules = CampaignRules.model_validate(payload.get("rules") or {})
    rows = [[_link(payload, "点击参与抽奖", "join")]]
    bonuses = []
    if rules.invitation_enabled:
        bonuses.append(_link(payload, "邀请入口", "invite"))
    if rules.bonus_types:
        bonuses.append(_link(payload, "我的加成", "bonus"))
    if bonuses:
        rows.append(bonuses)
    folder = payload.get("presentation", {}).get("folder_link")
    if folder:
        rows.append([InlineKeyboardButton(text="一键加入群和频道", url=str(folder))])
    rows.append([share_to_group_button(payload["campaign_ref"])])
    return style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows))


def _link(payload, text, action):
    return InlineKeyboardButton(text=text, url=campaign_link(payload, action))
