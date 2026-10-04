from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts
from tg_lottery.application.invitation_models import accepts_attribution
from tg_lottery.application.query_invites import ReferralLanding
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.invitation_text import invitation_purpose
from tg_lottery.telegram.member_bonus_text import historical_outcome_status
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
    outcome = None
    if historical_outcome_status(landing.campaign, outcome=None) is not None:
        outcome = await flow._queries.campaign_outcome(landing.campaign.id)
    membership = await _membership_state(flow, landing, user.id)
    if membership is True and flow._invitations is not None:
        await flow._invitations.record_existing_member(
            landing.route.id,
            user.id,
            utcnow(),
            display_name=user.full_name,
        )
    text = _landing_text(landing, membership, now=utcnow(), outcome=outcome)
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


def _landing_text(
    landing: ReferralLanding,
    membership: bool | None,
    *,
    now: datetime,
    outcome: CampaignOutcomeFacts | None = None,
) -> str:
    if landing.campaign.rules.get("participation_rules_version") == "v3.0":
        return _v3_landing_text(landing, membership, now=now, outcome=outcome)
    identity = f"{landing.campaign.name}"
    state = _landing_state(landing, membership, now=now, outcome=outcome)
    timezone = landing.campaign.timezone
    deadline = format_public_time(_effective_bonus_deadline(landing), timezone)
    draw_time = _draw_time_text(landing, outcome=outcome)
    verification = "\n⚠ 暂时无法核验你是否已在群内，入群后仍需核验。" if membership is None else ""
    return (
        f"👥 {identity}\n\n{state}{verification}\n\n"
        f"最晚计奖入群时间：{deadline}\n{draw_time}\n\n"
        "加入该群不等于自动报名本次抽奖。\n"
        "此邀请入口仅属于本次抽奖，结束后如入群入口仍有效，可继续入群，不计入下次抽奖。"
    )


def _landing_state(
    landing: ReferralLanding,
    membership: bool | None,
    *,
    now: datetime,
    outcome: CampaignOutcomeFacts | None = None,
) -> str:
    historical_status = historical_outcome_status(landing.campaign, outcome=outcome)
    if historical_status is not None:
        return (
            f"{historical_status}。\n"
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


def _draw_time_text(
    landing: ReferralLanding, *, outcome: CampaignOutcomeFacts | None = None
) -> str:
    campaign = landing.campaign
    if historical_outcome_status(campaign, outcome=outcome) is None:
        return f"开奖时间：{format_public_time(campaign.draw_at, campaign.timezone)}"
    if outcome is not None and outcome.kind == "drawn" and outcome.completed_at is not None:
        return f"实际开奖时间：{format_public_time(outcome.completed_at, campaign.timezone)}"
    return f"计划开奖时间：{format_public_time(campaign.draw_at, campaign.timezone)}"


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


def _v3_landing_text(
    landing: ReferralLanding,
    membership: bool | None,
    *,
    now: datetime,
    outcome: CampaignOutcomeFacts | None,
) -> str:
    from tg_lottery.presentation.participation_rules import gate_lines
    from tg_lottery.telegram.creator_invite_retention import retention_text

    rules = CampaignRules.model_validate(landing.campaign.rules)
    chat_id = landing.target.chat_id_snapshot
    purpose = invitation_purpose(rules, chat_id)
    active = _bonus_active(landing, now)
    status = "已有成员，本次打开不产生有效新邀请" if membership is True else "仅真实新成员可计入"
    if not active:
        status = "本场新增邀请已关闭；现存入口如可用仍可入群"
    chats = tuple(landing.campaign.presentation.get("participation_groups") or [])
    return (
        f"{landing.campaign.name}\n邀请用途: {purpose}\n{status}\n"
        f"有效邀请要求: {retention_text(rules.invite.retention_hours)}\n"
        f"最晚有效入群: {format_public_time(_effective_bonus_deadline(landing), rules.timezone)}\n"
        f"报名截止: {format_public_time(landing.campaign.cutoff_at, rules.timezone)}\n"
        f"{_draw_time_text(landing, outcome=outcome)}\n"
        + "\n".join(gate_lines(rules, chats))
        + "\n受邀人无需完成自己的抽奖门槛；加入群不会自动登记本人抽奖。"
    )
