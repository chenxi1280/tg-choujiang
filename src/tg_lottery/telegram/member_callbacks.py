"""群内卡片 callback 处理: 直接参与、获取加成链接与查看活动规则。"""

from typing import TYPE_CHECKING
from uuid import UUID

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.public_refs import is_campaign_public_ref
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_bonus_permissions import inspect_bonus_permissions
from tg_lottery.telegram.member_group_invites import build_group_invite_payload
from tg_lottery.telegram.member_popups import popup_already_participated, popup_failure

if TYPE_CHECKING:
    from tg_lottery.telegram.member_flow import MemberFlow
    from tg_lottery.telegram.personal_flow import PersonalFlow


async def record_callback(
    flow: "MemberFlow", personal: "PersonalFlow", query: CallbackQuery, *, data: str
) -> bool:
    if data.startswith("member:tickets:"):
        await flow.show_tickets(
            query.message,
            query.from_user.id,
            public_ref=data.removeprefix("member:tickets:"),
        )
        return True
    if data.startswith("member:invites:"):
        public_ref, page = _invitation_record_reference(data)
        await flow.show_invitations(
            query.message, query.from_user.id, public_ref=public_ref, page=page
        )
        return True
    if data in {"member:tickets", "member:invites"}:
        await personal.show_joined(query.message)
        return True
    return False


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
    if not rules.bonus_types:
        await safe_answer(query, "本期抽奖没有开启加成", alert=True)
        return
    if not await _registered_for_bonus(flow, campaign, rules, user_id=query.from_user.id):
        await safe_answer(query, "请先点击「立即参与」完成全部参与群资格校验", alert=True)
        return
    try:
        text, markup = await _bonus_payload(flow, campaign, rules, user_id=query.from_user.id)
        await flow._bot.send_message(query.from_user.id, text, reply_markup=markup)
        await safe_answer(query, "加成进度已私聊发送给你", alert=False)
    except TelegramAPIError:
        await query.message.answer(
            "查看加成需要先和机器人建立私聊，请点击下方按钮：",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="私聊机器人",
                            url=(
                                f"https://t.me/{flow._bot_username}"
                                f"?start=bonus_{campaign.public_ref}"
                            ),
                        )
                    ]
                ]
            ),
        )
        await safe_answer(query, "请先私聊机器人一次", alert=True)


async def show_bonus_message(
    flow: "MemberFlow",
    message: Message,
    campaign_reference: str | UUID,
    *,
    user_id: int,
) -> None:
    campaign = await flow._queries.campaign_by_reference(campaign_reference)
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    rules = CampaignRules.model_validate(campaign.rules)
    if not rules.bonus_types:
        raise UserFacingError("本期抽奖没有开启加成")
    if not await _registered_for_bonus(flow, campaign, rules, user_id=user_id):
        raise UserFacingError("请先点击「立即参与」完成全部参与群资格校验")
    text, markup = await _bonus_payload(flow, campaign, rules, user_id=user_id)
    await message.answer(text, reply_markup=markup)


async def _bonus_payload(
    flow: "MemberFlow", campaign: object, rules: CampaignRules, *, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    if rules.group_invite_rules or rules.group_activity_rules:
        unavailable = await inspect_bonus_permissions(flow._bot, rules)
        return await build_group_invite_payload(
            campaign,
            user_id=user_id,
            page=0,
            queries=flow._queries,
            cipher=flow._cipher,
            bot_username=flow._bot_username,
            unavailable=unavailable,
        )
    if not rules.invite_bonus_enabled:
        raise UserFacingError("本期抽奖没有可展示的加成")
    return await flow._invite_link_payload(campaign, user_id)


async def _registered_for_bonus(
    flow: "MemberFlow", campaign: object, rules: CampaignRules, *, user_id: int
) -> bool:
    if rules.group_invite_rules or rules.group_activity_rules:
        participant = await flow._participants.eligible_participant(campaign.id, user_id)
        return participant is not None
    link = await flow._queries.invite_link_for_participant(campaign.id, user_id)
    return link is not None


async def group_invite_callback(flow: "MemberFlow", query: CallbackQuery, payload: str) -> None:
    reference, separator, raw_chat_id = payload.rpartition(":")
    if not separator:
        raise UserFacingError("邀请群参数无效")
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    await flow._participants.request_group_invite_link(
        campaign.id,
        query.from_user.id,
        int(raw_chat_id),
        bot_username=flow._bot_username,
    )
    await safe_answer(query, "链接已提交；明确失败的请求会重新排队", alert=False)


async def invite_page_callback(flow: "MemberFlow", query: CallbackQuery, payload: str) -> None:
    reference, separator, raw_page = payload.rpartition(":")
    if not separator or not raw_page.isdecimal():
        raise UserFacingError("邀请链接页码无效")
    await flow.show_invite_link(
        query.message,
        reference,
        user_id=query.from_user.id,
        page=int(raw_page),
    )


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


def _invitation_record_reference(data: str) -> tuple[str, int]:
    value = data.removeprefix("member:invites:")
    public_ref, separator, raw_page = value.rpartition(":")
    if not separator or not is_campaign_public_ref(public_ref):
        raise ValueError("邀请记录按钮参数无效")
    page = int(raw_page)
    if page < 0:
        raise ValueError("邀请记录页码无效")
    return public_ref, page
