from datetime import datetime, timedelta
from urllib.parse import quote

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.invitation_models import accepts_attribution
from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_bonus_text import (
    HISTORICAL_CAMPAIGN_STATES,
    campaign_bonus_completed,
    invite_bonus_completed,
    invite_list_prompt,
)
from tg_lottery.telegram.public_projection import format_public_time


def invite_link_text(campaign_name: str, *, note: str, link: str) -> str:
    return f"🔗 {campaign_name} 专属邀请链接\n\n{note}\n\n复制下方链接分享邀请：\n{link}"


def invite_link_keyboard(link: str, campaign_name: str, *, active: bool) -> InlineKeyboardMarkup:
    share = "https://t.me/share/url?url=" + quote(link, safe="")
    share_text = f"邀请你参加「{campaign_name}」抽奖" if active else "邀请你加入群聊"
    share += "&text=" + quote(share_text, safe="")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="复制邀请链接" if active else "复制入群链接",
                    copy_text=CopyTextButton(text=link),
                )
            ],
            [InlineKeyboardButton(text="打开邀请入口" if active else "打开入群入口", url=link)],
            [InlineKeyboardButton(text="转发给好友" if active else "转发入群", url=share)],
        ]
    )


def member_keyboard(
    campaign_reference: str | None = None,
    bonus_enabled: bool = False,
    *,
    invite_enabled: bool | None = None,
    invite_gate_enabled: bool = False,
) -> InlineKeyboardMarkup:
    ticket_callback = (
        f"member:tickets:{campaign_reference}" if campaign_reference else "member:tickets"
    )
    invite_callback = (
        f"member:invites:{campaign_reference}:0" if campaign_reference else "member:invites"
    )
    rows = [[InlineKeyboardButton(text="我的抽奖次数", callback_data=ticket_callback)]]
    if (bonus_enabled or invite_gate_enabled) and campaign_reference:
        rows.append(
            [
                InlineKeyboardButton(
                    text="邀请门槛与入口"
                    if invite_gate_enabled and not bonus_enabled
                    else "我的加成",
                    callback_data=f"member:bonus:{campaign_reference}",
                )
            ]
        )
    show_invitation_record = bonus_enabled if invite_enabled is None else invite_enabled
    if show_invitation_record:
        rows.append([InlineKeyboardButton(text="邀请记录", callback_data=invite_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def legacy_invite_link_payload(
    campaign: object, *, user_id: int, queries: object, cipher: object
) -> tuple[str, InlineKeyboardMarkup]:
    if campaign.state in HISTORICAL_CAMPAIGN_STATES:
        rules = CampaignRules.model_validate(campaign.rules)
        outcome = await queries.campaign_outcome(campaign.id)
        notice = invite_list_prompt(
            campaign, retention_hours=rules.invite.retention_hours, outcome=outcome
        )
        return (
            f"{campaign.name}：{notice}\n"
            "不再计算本期或以后任何抽奖加成；历史邀请记录和已确认加成保留。",
            member_keyboard(campaign.public_ref, invite_enabled=True),
        )
    stored = await queries.invite_link_for_participant(campaign.id, user_id)
    if stored is None:
        raise UserFacingError("本期专属链接正在创建，或你尚未完成报名，请稍后再点")
    unavailable = unavailable_invite_link_note(stored, now=utcnow())
    if unavailable is not None:
        return (
            f"{campaign.name}：{unavailable}",
            member_keyboard(campaign.public_ref, invite_enabled=True),
        )
    if stored.encrypted_url is None:
        raise UserFacingError("本期专属链接正在创建，或你尚未完成报名，请稍后再点")
    link = cipher.decrypt(stored.encrypted_url, context=f"invite-link:{stored.id}")
    note, active = legacy_invite_note(campaign)
    return invite_link_text(campaign.name, note=note, link=link), invite_link_keyboard(
        link, campaign.name, active=active
    )


def unavailable_invite_link_note(link: object, *, now: datetime) -> str | None:
    if link.revoked_at is not None:
        return "个人邀请链接已撤销；请返回活动查看当前入口与记录。"
    if link.expire_at is not None and link.expire_at <= now:
        return "个人邀请链接已过期；请返回活动查看当前入口与记录。"
    return None


def legacy_invite_note(campaign: object) -> tuple[str, bool]:
    rules = CampaignRules.model_validate(campaign.rules)
    deadline = campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours)
    now = utcnow()
    active = not invite_bonus_completed(
        campaign, retention_hours=rules.invite.retention_hours
    ) and accepts_attribution(
        campaign.state,
        preheat_at=campaign.preheat_at,
        cutoff_at=campaign.cutoff_at,
        event_at=now,
    )
    if active:
        return (
            f"最晚计奖入群：{format_public_time(deadline, campaign.timezone)}；"
            "通过该链接加入并满足留群及活动条件后，才会计算本期邀请加成。",
            True,
        )
    if not campaign_bonus_completed(campaign) and now < campaign.preheat_at:
        return "本期邀请加成尚未开始；当前链接仅用于入群，不计加成。", False
    if not campaign_bonus_completed(campaign):
        return "最晚计奖入群时间已过；此链接仍可入群，新入群不计加成，仅核验此前邀请。", False
    return "本期已截止；此链接仍可入群，但不再计算本期或以后任何抽奖加成。", False
