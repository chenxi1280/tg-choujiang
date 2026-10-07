"""群内卡片 callback 处理: 直接参与、获取加成链接与查看活动规则。"""

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.public_refs import is_campaign_public_ref
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_bonus_permissions import inspect_bonus_permissions
from tg_lottery.telegram.member_bonus_read import bonus_read_payload
from tg_lottery.telegram.member_bonus_views import (
    build_bonus_category,
    build_bonus_group_detail,
)
from tg_lottery.telegram.member_popups import popup_already_participated, popup_failure
from tg_lottery.telegram.member_record_delivery import private_record_message
from tg_lottery.telegram.member_records import show_invitation_records
from tg_lottery.telegram.platform_inviter import verify_platform_inviter

if TYPE_CHECKING:
    from tg_lottery.telegram.member_flow import MemberFlow
    from tg_lottery.telegram.personal_flow import PersonalFlow


async def record_callback(
    flow: "MemberFlow", personal: "PersonalFlow", query: CallbackQuery, *, data: str
) -> bool:
    try:
        handled = await _show_record_callback(flow, personal, query, data=data)
    except TelegramForbiddenError:
        logging.getLogger(__name__).warning(
            "Participant record private delivery forbidden", exc_info=True
        )
        await _record_acknowledgement(
            flow,
            query,
            text="私聊发送失败，请先打开机器人或解除屏蔽，再私聊发送 /join 查看参与记录。",
            alert=True,
        )
        return True
    if handled:
        await _record_acknowledgement(flow, query)
    return handled


async def _record_acknowledgement(
    flow: "MemberFlow", query: CallbackQuery, *, text: str | None = None, alert: bool = False
) -> None:
    try:
        await query.model_copy().as_(flow._bot).answer(text, show_alert=alert)
    except TelegramBadRequest as exc:
        logging.getLogger(__name__).warning(
            "Participant record callback acknowledgement rejected by Telegram",
            extra={"callback_query_id": query.id, "error": str(exc)},
        )


async def _show_record_callback(
    flow: "MemberFlow", personal: "PersonalFlow", query: CallbackQuery, *, data: str
) -> bool:
    if not data.startswith(("m:ir:", "member:tickets", "member:invites")):
        return False
    message = private_record_message(
        query.message, flow._bot, user_id=query.from_user.id, user=query.from_user
    )
    if data.startswith("m:ir:"):
        reference, chat_id, group_page, status_filter, page = _group_record_reference(data)
        await show_invitation_records(
            message,
            flow._queries,
            query.from_user.id,
            public_ref=reference,
            page=page,
            chat_id=chat_id,
            group_page=group_page,
            status_filter=status_filter,
        )
        return True
    if data.startswith("member:tickets:"):
        await flow.show_tickets(
            message,
            query.from_user.id,
            public_ref=data.removeprefix("member:tickets:"),
        )
        return True
    if data.startswith("member:invites:"):
        public_ref, page = _invitation_record_reference(data)
        await flow.show_invitations(message, query.from_user.id, public_ref=public_ref, page=page)
        return True
    if data in {"member:tickets", "member:invites"}:
        await personal.show_joined(message)
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
    if not rules.bonus_types and not rules.invite_admission_enabled:
        if not await _queue_group_reminder(
            flow, query, campaign, text="本期抽奖没有开启可用的邀请或发言加成。"
        ):
            await safe_answer(query, "本期抽奖没有开启加成", alert=True)
        return
    try:
        text, markup = await _bonus_payload(flow, campaign, rules, user_id=query.from_user.id)
        await flow._bot.send_message(query.from_user.id, text, reply_markup=markup)
        await safe_answer(query, "加成进度已私聊发送给你", alert=False)
    except UserFacingError as exc:
        if await send_public_reminder_for_error(flow, query, campaign, error=exc):
            return
        await safe_answer(query, str(exc), alert=True)
    except TelegramAPIError:
        await safe_answer(query, "请先私聊机器人，再查看加成详情", alert=True)


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
    if not rules.bonus_types and not rules.invite_admission_enabled:
        raise UserFacingError("本期抽奖没有开启加成")
    text, markup = await _bonus_payload(flow, campaign, rules, user_id=user_id)
    await message.answer(text, reply_markup=markup)


async def _bonus_payload(
    flow: "MemberFlow", campaign: object, rules: CampaignRules, *, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    return await bonus_read_payload(flow, campaign, rules, user_id=user_id)


async def group_invite_callback(flow: "MemberFlow", query: CallbackQuery, payload: str) -> None:
    reference, separator, raw_chat_id = payload.rpartition(":")
    if not separator:
        raise UserFacingError("邀请群参数无效")
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    try:
        await verify_platform_inviter(flow, campaign, user=query.from_user)
        await flow._participants.request_group_invite_link(
            campaign.id,
            query.from_user.id,
            int(raw_chat_id),
            bot_username=flow._bot_username,
        )
    except UserFacingError as exc:
        if await send_public_reminder_for_error(flow, query, campaign, error=exc):
            return
        raise
    await safe_answer(query, "链接已提交；明确失败的请求会重新排队", alert=False)


async def invite_page_callback(flow: "MemberFlow", query: CallbackQuery, payload: str) -> None:
    reference, separator, raw_page = payload.rpartition(":")
    if not separator or not raw_page.isdecimal():
        raise UserFacingError("邀请链接页码无效")
    await _deliver_invite_callback(flow, query, reference, page=int(raw_page))


async def invite_link_callback(flow: "MemberFlow", query: CallbackQuery, reference: str) -> None:
    await _deliver_invite_callback(flow, query, reference)


async def _deliver_invite_callback(
    flow: "MemberFlow", query: CallbackQuery, reference: str, *, page: int = 0
) -> None:
    message = private_record_message(
        query.message, flow._bot, user_id=query.from_user.id, user=query.from_user
    )
    try:
        await flow.show_invite_link(message, reference, user_id=query.from_user.id, page=page)
    except UserFacingError as exc:
        campaign = await flow._queries.campaign_by_reference(reference)
        if campaign is None or not await send_public_reminder_for_error(
            flow, query, campaign, error=exc
        ):
            raise
        return
    except TelegramAPIError:
        logging.getLogger(__name__).warning("Invite private delivery failed", exc_info=True)
        await safe_answer(query, "请先私聊机器人，再查看邀请入口", alert=True)
        return
    await safe_answer(query, "邀请入口已私聊发送给你", alert=False)


async def bonus_category_callback(
    flow: "MemberFlow", query: CallbackQuery, payload: str, *, category: str
) -> None:
    reference, page = _reference_page(payload, label="加成列表")
    campaign, rules = await _eligible_bonus_campaign(flow, reference, query.from_user.id)
    unavailable = await inspect_bonus_permissions(flow._bot, rules)
    text, markup = await build_bonus_category(
        campaign,
        user_id=query.from_user.id,
        category=category,
        page=page,
        queries=flow._queries,
        cipher=flow._cipher,
        bot_username=flow._bot_username,
        unavailable=unavailable,
    )
    await query.message.answer(text, reply_markup=markup)


async def bonus_group_detail_callback(
    flow: "MemberFlow", query: CallbackQuery, payload: str, *, category: str
) -> None:
    reference, chat_id, return_page = _reference_chat_page(payload, label="加成群")
    campaign, rules = await _eligible_bonus_campaign(flow, reference, query.from_user.id)
    unavailable = await inspect_bonus_permissions(flow._bot, rules)
    text, markup = await build_bonus_group_detail(
        campaign,
        user_id=query.from_user.id,
        category=category,
        chat_id=chat_id,
        return_page=return_page,
        queries=flow._queries,
        cipher=flow._cipher,
        bot_username=flow._bot_username,
        unavailable=unavailable,
    )
    await query.message.answer(text, reply_markup=markup)


async def bonus_navigation_callback(flow: "MemberFlow", query: CallbackQuery, data: str) -> bool:
    routes = (
        ("m:bo:", show_bonus_message, None),
        ("member:bonus_invites:", bonus_category_callback, "invites"),
        ("member:bonus_activity:", bonus_category_callback, "activity"),
        ("m:id:", bonus_group_detail_callback, "invites"),
        ("m:ad:", bonus_group_detail_callback, "activity"),
    )
    matched = next((route for route in routes if data.startswith(route[0])), None)
    if matched is None:
        return False
    prefix, handler, category = matched
    payload = data.removeprefix(prefix)
    reference = payload if category is None else _bonus_error_reference(prefix, payload)
    try:
        if category is None:
            await handler(flow, query.message, payload, user_id=query.from_user.id)
        else:
            await handler(flow, query, payload, category=category)
    except UserFacingError as exc:
        campaign = await flow._queries.campaign_by_reference(reference)
        if campaign is None or not await send_public_reminder_for_error(
            flow, query, campaign, error=exc
        ):
            raise
    return True


async def send_public_reminder_for_error(
    flow: "MemberFlow", query: CallbackQuery, campaign: object, *, error: Exception
) -> bool:
    message = query.message
    if not isinstance(message, Message) or message.chat.type == "private":
        return False
    text = str(error)
    if not _temporary_reminder_error(text):
        return False
    await _queue_group_reminder(flow, query, campaign, text=text)
    return True


async def _queue_group_reminder(
    flow: "MemberFlow", query: CallbackQuery, campaign: object, *, text: str
) -> bool:
    message = query.message
    if not isinstance(message, Message) or message.chat.type == "private":
        return False
    await flow._participants.queue_temporary_reminder(
        campaign_id=campaign.id,
        recipient_chat_id=message.chat.id,
        text=text,
        business_key=f"temporary_reminder:{campaign.id}:{query.id}",
        campaign_ref=campaign.public_ref,
    )
    await safe_answer(query, text, alert=True)
    return True


REGISTRATION_REMINDER = "请先点击「参与抽奖」完成报名及参与条件校验，再查看邀请或对应加成。"

_TEMPORARY_REMINDER_MESSAGES = frozenset(
    {
        REGISTRATION_REMINDER,
        "仅合格参与者可获取本活动群邀请链接",
        "该群未开启邀请门槛或邀请加成",
        "本期抽奖没有开启邀请加成",
        "本期抽奖没有开启加成",
        "本期抽奖没有可展示的加成",
        "本期抽奖没有可展示的逐群加成",
        "本期专属链接正在创建，或你尚未完成报名，请稍后再点",
        "请先登记本场活动；已有本人记录始终可读，不要求重新报名",
        "该群未开启发言加成",
        "该群未开启邀请门槛或加成",
        "参与群不存在或已移除",
    }
)


def _temporary_reminder_error(text: str) -> bool:
    return text in _TEMPORARY_REMINDER_MESSAGES


async def send_bonus_navigation_reminder(flow, query, error) -> bool:
    if not _temporary_reminder_error(str(error)):
        return False
    prefixes = ("m:bo:", "m:id:", "m:ad:", "member:bonus_invites:", "member:bonus_activity:")
    data = query.data or ""
    prefix = next((item for item in prefixes if data.startswith(item)), None)
    if prefix is None:
        return False
    payload = data.removeprefix(prefix)
    try:
        reference = payload if prefix == "m:bo:" else _bonus_error_reference(prefix, payload)
    except ValueError:
        return False
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None:
        return False
    return await send_public_reminder_for_error(flow, query, campaign, error=error)


def _bonus_error_reference(prefix: str, payload: str) -> str:
    if prefix.startswith("member:bonus_"):
        reference, _page = _reference_page(payload, label="加成列表")
        return reference
    reference, _chat_id, _return_page = _reference_chat_page(payload, label="加成群")
    return reference


async def rules_callback(
    flow: "MemberFlow", query: CallbackQuery, campaign_reference: str | UUID
) -> None:
    await safe_answer(query, "请点击新版卡片的规则按钮，在私聊中查看完整规则", alert=True)


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


async def _eligible_bonus_campaign(
    flow: "MemberFlow", reference: str, user_id: int
) -> tuple[object, CampaignRules]:
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    rules = CampaignRules.model_validate(campaign.rules)
    if await flow._queries.participant_campaign_detail(user_id, campaign.public_ref) is None:
        raise UserFacingError(REGISTRATION_REMINDER)
    return campaign, rules


def _reference_page(payload: str, *, label: str) -> tuple[str, int]:
    reference, separator, raw_page = payload.rpartition(":")
    if not separator or not is_campaign_public_ref(reference) or not raw_page.isdecimal():
        raise ValueError(f"{label}按钮参数无效")
    return reference, int(raw_page)


def _reference_chat_page(payload: str, *, label: str) -> tuple[str, int, int]:
    parts = payload.rsplit(":", 2)
    if len(parts) != 3 or not is_campaign_public_ref(parts[0]):
        raise ValueError(f"{label}按钮参数无效")
    try:
        chat_id, return_page = int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError(f"{label}按钮参数无效") from exc
    if return_page < 0:
        raise ValueError(f"{label}返回页码无效")
    return parts[0], chat_id, return_page


def _group_record_reference(data: str) -> tuple[str, int, int, str, int]:
    value = data.removeprefix("m:ir:")
    parts = value.rsplit(":", 4)
    if len(parts) != 5 or not is_campaign_public_ref(parts[0]):
        raise ValueError("逐群邀请记录按钮参数无效")
    reference, raw_chat_id, raw_group_page, status_filter, raw_page = parts
    if status_filter not in {"all", "pending", "valid", "invalid"}:
        raise ValueError("逐群邀请记录筛选无效")
    try:
        chat_id = int(raw_chat_id)
        group_page, page = int(raw_group_page), int(raw_page)
    except ValueError as exc:
        raise ValueError("逐群邀请记录按钮参数无效") from exc
    if page < 0 or group_page < 0:
        raise ValueError("逐群邀请记录页码无效")
    return reference, chat_id, group_page, status_filter, page
