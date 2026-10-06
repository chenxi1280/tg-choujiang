from tg_lottery.domain.errors import UserFacingError


async def show_share_card(flow, message, reference):
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    await flow._participants.request_share_card(
        campaign.id, message.from_user.id, bot_username=flow._bot_username
    )
    await message.answer("分享卡片已提交发送到本私聊；收到后长按卡片转发，封面和正文可一起转发。")
