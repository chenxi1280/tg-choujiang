"""群内卡片 callback 处理: 直接参与、获取加成链接与查看活动规则。"""

from typing import TYPE_CHECKING
from uuid import UUID

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_popups import popup_already_participated, popup_failure

if TYPE_CHECKING:
    from tg_lottery.telegram.member_flow import MemberFlow


async def register_callback(
    flow: "MemberFlow", query: CallbackQuery, campaign_reference: str | UUID
) -> None:
    try:
        await flow.register(
            query.message,
            campaign_reference,
            user=query.from_user,
            observed_at=utcnow(),
            callback=query,
        )
    except UserFacingError as exc:
        await safe_answer(query, str(exc), alert=True)


async def alert_failure(
    callback: CallbackQuery,
    campaign: object,
    state: QualificationState,
    *,
    observations: tuple[dict[str, object], ...],
) -> None:
    await safe_answer(callback, popup_failure(campaign, state, observations), alert=True)


async def already_reply(callback: CallbackQuery | None, message: Message, total: int) -> None:
    text = popup_already_participated(total)
    if callback is not None:
        await safe_answer(callback, text, alert=False)
    else:
        await message.answer(text)


async def bonus_callback(
    flow: "MemberFlow", query: CallbackQuery, campaign_reference: str | UUID
) -> None:
    campaign = await flow._queries.campaign_by_reference(campaign_reference)
    if campaign is None:
        await safe_answer(query, "活动不存在或已经失效", alert=True)
        return
    rules = CampaignRules.model_validate(campaign.rules)
    if not rules.invite_bonus_enabled:
        await safe_answer(query, "本期抽奖没有开启邀请加成", alert=True)
        return
    registered = await flow._queries.invite_link_for_participant(campaign.id, query.from_user.id)
    if registered is None:
        await safe_answer(query, "请先点击「立即参与」完成报名", alert=True)
        return
    message = query.message
    try:
        await flow._send_invite_link_to(query.from_user.id, campaign)
        await safe_answer(query, "专属邀请链接已私聊发送给你", alert=False)
    except TelegramAPIError:
        await message.answer(
            "获取加成链接需要先和机器人建立私聊，请点击下方按钮后重试：",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="私聊机器人",
                            url=(
                                f"https://t.me/{flow._bot_username}"
                                f"?start=invite_{campaign.public_ref}"
                            ),
                        )
                    ]
                ]
            ),
        )
        await safe_answer(query, "请先私聊机器人一次", alert=True)


async def rules_callback(
    flow: "MemberFlow", query: CallbackQuery, campaign_reference: str | UUID
) -> None:
    await flow.show_rules(query.message, campaign_reference)
    await safe_answer(query, "活动规则已发送", alert=False)


async def safe_answer(query: CallbackQuery, text: str, *, alert: bool) -> None:
    try:
        await query.answer(text[:200], show_alert=alert)
    except TelegramBadRequest:
        pass
