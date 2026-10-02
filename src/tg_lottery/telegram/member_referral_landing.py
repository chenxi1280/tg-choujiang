from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.invitation_models import accepts_attribution
from tg_lottery.application.query_invites import ReferralLanding
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.public_projection import format_public_time

if TYPE_CHECKING:
    from tg_lottery.telegram.member_flow import MemberFlow

_MEMBER_STATUSES = frozenset({"member", "administrator", "creator"})


async def show_referral_landing(flow: MemberFlow, message: Message, public_ref: str) -> None:
    landing = await flow._queries.referral_landing_by_reference(public_ref)
    if landing is None:
        raise UserFacingError("本期邀请入口不存在或已经失效")
    user = message.from_user
    if user is None:
        raise ValueError("Telegram message has no sender")
    membership = await _membership_state(flow, landing, user.id)
    if membership is True and flow._invitations is not None:
        await flow._invitations.record_existing_member(
            landing.route.id,
            user.id,
            utcnow(),
            display_name=user.full_name,
        )
    text = _landing_text(landing, membership, now=utcnow())
    if _group_url(flow, landing) is None:
        text += "\n\n⚠ 入群入口当前不可用，请联系群管理员更新链接。"
    markup = _landing_keyboard(flow, landing, membership)
    await message.answer(text, reply_markup=markup)


async def _membership_state(
    flow: MemberFlow, landing: ReferralLanding, user_id: int
) -> bool | None:
    try:
        member = await flow._bot.get_chat_member(landing.target.chat_id_snapshot, user_id)
    except TelegramAPIError:
        return None
    status = str(member.status)
    if status in _MEMBER_STATUSES:
        return True
    if status == "restricted":
        return bool(getattr(member, "is_member", False))
    if status in {"left", "kicked"}:
        return False
    return None


def _landing_text(landing: ReferralLanding, membership: bool | None, *, now: datetime) -> str:
    identity = f"{landing.campaign.name}"
    state = _landing_state(landing, membership, now=now)
    timezone = landing.campaign.timezone
    deadline = format_public_time(_effective_bonus_deadline(landing), timezone)
    draw_at = format_public_time(landing.campaign.draw_at, timezone)
    verification = "\n⚠ 暂时无法核验你是否已在群内，入群后仍需核验。" if membership is None else ""
    return (
        f"👥 {identity}\n\n{state}{verification}\n\n"
        f"最晚计奖入群时间：{deadline}\n开奖时间：{draw_at}\n\n"
        "加入该群不等于自动报名本次抽奖。\n"
        "此邀请入口仅属于本次抽奖，结束后如入群入口仍有效，可继续入群，不计入下次抽奖。"
    )


def _landing_state(landing: ReferralLanding, membership: bool | None, *, now: datetime) -> str:
    if landing.campaign.state is CampaignState.CANCELLED:
        return (
            "本次抽奖已经取消。\n"
            f"该入口不再产生抽奖加成；如群入口仍可用，你仍可加入「{landing.target.title_snapshot}」。"
        )
    if membership is True:
        return (
            f"你已经是「{landing.target.title_snapshot}」成员。\n本次打开不会产生新成员邀请加成。"
        )
    if _registration_open(landing, now) and now < landing.campaign.preheat_at:
        return (
            "本期邀请加成尚未开始。\n"
            f"你仍可加入「{landing.target.title_snapshot}」，"
            "但当前入群不会给邀请人增加本期抽奖次数。"
        )
    if _bonus_active(landing, now):
        return (
            "你收到一位好友的邀请。\n"
            f"在最晚计奖时间前加入「{landing.target.title_snapshot}」，"
            "完成留存后可计入邀请人的本期加成。"
        )
    return (
        "本期邀请加成已经结束。\n"
        f"如群入口仍有效，你仍可加入「{landing.target.title_snapshot}」，"
        "但不会再给邀请人增加本期抽奖次数。"
    )


def _effective_bonus_deadline(landing: ReferralLanding) -> datetime:
    rules = CampaignRules.model_validate(landing.campaign.rules)
    cutoff = landing.campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours)
    return min(landing.route.bonus_join_deadline, cutoff)


def _bonus_active(landing: ReferralLanding, now: datetime) -> bool:
    return (
        landing.route.status == "active"
        and landing.campaign.state
        in {CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED}
        and accepts_attribution(
            landing.campaign.state,
            preheat_at=landing.campaign.preheat_at,
            cutoff_at=landing.campaign.cutoff_at,
            event_at=now,
        )
        and now <= _effective_bonus_deadline(landing)
    )


def _landing_keyboard(
    flow: MemberFlow, landing: ReferralLanding, membership: bool | None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    group_url = _group_url(flow, landing)
    if group_url:
        label = "打开群聊" if membership is True else f"加入 {landing.target.title_snapshot}"
        rows.append([InlineKeyboardButton(text=label, url=group_url)])
    if not _registration_open(landing, utcnow()):
        rows.append([InlineKeyboardButton(text="查看我的参与记录", callback_data="nav:join")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    ref = landing.campaign.public_ref
    rows.append(
        [
            InlineKeyboardButton(
                text="查看参与条件",
                url=f"https://t.me/{flow._bot_username}?start=rules_{ref}",
            ),
            InlineKeyboardButton(
                text="报名本次抽奖",
                url=f"https://t.me/{flow._bot_username}?start=join_{ref}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _group_url(flow: MemberFlow, landing: ReferralLanding) -> str | None:
    link = landing.link
    expires = getattr(link, "expire_at", None)
    usable = expires is None or expires > utcnow()
    if link is not None and link.encrypted_url is not None and link.revoked_at is None and usable:
        return flow._cipher.decrypt(link.encrypted_url, context=f"invite-link:{link.id}")
    if landing.target.public_join_url:
        return landing.target.public_join_url
    return None


def _registration_open(landing: ReferralLanding, now: datetime) -> bool:
    return (
        landing.campaign.state
        in {CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED}
        and now <= landing.campaign.cutoff_at
    )
