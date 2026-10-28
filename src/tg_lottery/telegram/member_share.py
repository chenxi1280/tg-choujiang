from aiogram.types import InlineKeyboardMarkup

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.campaign_share_keyboard import share_to_chat_button
from tg_lottery.telegram.ui_style import style_keyboard


async def show_share_card(flow, message, reference):
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    await flow._participants.request_share_card(
        campaign.id, message.from_user.id, bot_username=flow._bot_username
    )
    await message.answer(
        "分享卡片已提交发送到本私聊；收到后可长按卡片转发到群或频道，"
        "也可点击下方按钮选择群或频道，再点选卡片发送。",
        reply_markup=style_keyboard(
            InlineKeyboardMarkup(inline_keyboard=[[share_to_chat_button(campaign.public_ref)]])
        ),
    )
