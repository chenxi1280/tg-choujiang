from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.invitation_models import accepts_attribution
from tg_lottery.application.query_invites import GroupInviteLinkStatus
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import OutboxState
from tg_lottery.domain.rules import CampaignRules, GroupInviteRule

PAGE_SIZE = 6


async def build_group_invite_payload(
    campaign: object,
    *,
    user_id: int,
    page: int,
    queries: object,
    cipher: object,
    bot_username: str = "",
    unavailable: dict[int, str] | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    rules = CampaignRules.model_validate(campaign.rules)
    groups = _configured_groups(campaign, rules)
    progress = await queries.group_bonus_progress(campaign.id, user_id)
    if progress is None:
        raise ValueError("group bonus progress requires an existing participant")
    invite_progress = {item.chat_id: item for item in progress.invitations}
    link_statuses = await queries.group_invite_link_statuses(campaign.id, user_id)
    unavailable = unavailable or {}
    maximum = max(0, (len(groups) - 1) // PAGE_SIZE)
    page = min(maximum, max(0, page))
    visible = groups[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [f"🔋 {campaign.name} 我的加成", ""]
    if any(rules.invite_rule_for(int(group["id"])) for group in groups):
        lines.append("邀请加成：")
    invite_lines, rows = _invite_section(
        campaign,
        rules,
        visible,
        invite_progress=invite_progress,
        link_statuses=link_statuses,
        unavailable=unavailable,
        cipher=cipher,
        bot_username=bot_username,
    )
    lines.extend(invite_lines)
    lines.extend(
        _activity_progress_lines(
            campaign,
            rules,
            progress,
            unavailable=unavailable,
            visible_chat_ids={int(group["id"]) for group in visible},
        )
    )
    if maximum:
        rows.append(_page_row(campaign.public_ref, page, maximum))
    rows.extend(_footer_rows(campaign.public_ref))
    lines.extend(["", "链接只归因到对应群；机器人权限异常时请联系创建者修复管理员权限。"])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _invite_section(
    campaign: object,
    rules: CampaignRules,
    visible: list[dict[str, Any]],
    *,
    invite_progress: dict[int, object],
    link_statuses: dict[int, GroupInviteLinkStatus],
    unavailable: dict[int, str],
    cipher: object,
    bot_username: str,
) -> tuple[list[str], list[list[InlineKeyboardButton]]]:
    lines = []
    rows = []
    for group in visible:
        chat_id = int(group["id"])
        if chat_id in unavailable:
            if rules.activity_rule_for(chat_id) is None:
                lines.append(f"· {group['title']}：⚠ {unavailable[chat_id]}")
            rows.append(_permission_refresh_row(campaign.public_ref, group))
            continue
        rule = rules.invite_rule_for(chat_id)
        if rule is None:
            continue
        bonus_deadline = campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours)
        link_status = link_statuses.get(chat_id)
        progress_line = _invite_progress_line(
            str(group["title"]), rule, invite_progress.get(chat_id)
        )
        lines.append(
            progress_line + _invite_link_note(campaign, link_status, bonus_deadline=bonus_deadline)
        )
        rows.extend(
            _group_rows(
                campaign,
                group,
                link_status,
                cipher=cipher,
                bot_username=bot_username,
                bonus_deadline=bonus_deadline,
            )
        )
    return lines, rows


def _permission_refresh_row(reference: str, group: dict[str, Any]) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=f"🔄 重新检测 {group['title']}",
            callback_data=f"member:bonus:{reference}",
        )
    ]


def _configured_groups(campaign: object, rules: CampaignRules) -> list[dict[str, Any]]:
    presentation = getattr(campaign, "presentation", {}) or {}
    records = presentation.get("participation_groups") or []
    enabled = {
        int(chat_id)
        for chat_id, rule in (
            *rules.group_invite_rules.items(),
            *rules.group_activity_rules.items(),
        )
        if rule.enabled
    }
    groups = [
        {
            "id": int(item["id"]),
            "title": str(item.get("title") or item["id"]),
            "join_url": item.get("join_url"),
        }
        for item in records
        if item.get("id") is not None and int(item["id"]) in enabled
    ]
    known = {int(item["id"]) for item in groups}
    groups.extend({"id": chat_id, "title": str(chat_id)} for chat_id in enabled - known)
    return groups


def _group_rows(
    campaign: object,
    group: dict[str, Any],
    status: GroupInviteLinkStatus | None,
    *,
    cipher: object,
    bot_username: str = "",
    bonus_deadline: datetime | None = None,
) -> list[list[InlineKeyboardButton]]:
    title = str(group["title"])
    stored = status.link if status is not None else None
    if stored is None or stored.encrypted_url is None:
        return _missing_link_row(campaign, group, status, bonus_deadline=bonus_deadline)
    if getattr(stored, "revoked_at", None) is not None:
        return _revoked_link_row(campaign, group, bonus_deadline=bonus_deadline)
    link = cipher.decrypt(stored.encrypted_url, context=f"invite-link:{stored.id}")
    route = status.route if status is not None else None
    referral = _referral_url(bot_username, route)
    if referral:
        return _referral_rows(campaign, title, referral, group_link=link, route=route)
    if not campaign_link_active(campaign):
        return [[InlineKeyboardButton(text=f"打开 {title}（加成已截止）", url=link)]]
    share = "https://t.me/share/url?url=" + quote(link, safe="")
    share += "&text=" + quote(f"邀请你加入「{title}」参加抽奖", safe="")
    return [
        [
            InlineKeyboardButton(text="复制链接", copy_text=CopyTextButton(text=link)),
            InlineKeyboardButton(text="转发给好友", url=share),
        ]
    ]


def _referral_url(bot_username: str, route: object | None) -> str | None:
    if route is None or not bot_username:
        return None
    username = bot_username.removeprefix("@")
    return f"https://t.me/{username}?start=ref_{route.public_ref}"


def _referral_rows(
    campaign: object,
    title: str,
    referral: str,
    *,
    group_link: str,
    route: object,
) -> list[list[InlineKeyboardButton]]:
    active = (
        campaign_link_active(campaign)
        and utcnow() <= route.bonus_join_deadline
        and route.status == "active"
    )
    copy_label = "复制本期邀请链接" if active else "复制入群链接（不计加成）"
    share_label = "转发邀请" if active else "转发入群"
    share_text = f"邀请你加入「{title}」参加本期抽奖" if active else f"邀请你加入「{title}」"
    share = "https://t.me/share/url?url=" + quote(referral, safe="")
    share += "&text=" + quote(share_text, safe="")
    return [
        [
            InlineKeyboardButton(text=copy_label, copy_text=CopyTextButton(text=referral)),
            InlineKeyboardButton(text=share_label, url=share),
        ],
        [InlineKeyboardButton(text=f"直接打开 {title}", url=group_link)],
    ]


def _missing_link_row(
    campaign: object,
    group: dict[str, Any],
    status: GroupInviteLinkStatus | None,
    *,
    bonus_deadline: datetime | None = None,
) -> list[list[InlineKeyboardButton]]:
    title = str(group["title"])
    state = status.delivery_state if status is not None else None
    refresh = f"member:bonus:{campaign.public_ref}"
    if not _link_creation_active(campaign, bonus_deadline):
        join_url = group.get("join_url")
        return (
            [[InlineKeyboardButton(text=f"打开 {title}（不计加成）", url=str(join_url))]]
            if join_url
            else []
        )
    if state is OutboxState.UNKNOWN_HOLD:
        return [[InlineKeyboardButton(text=f"🔄 核验 {title} 链接状态", callback_data=refresh)]]
    if state in {
        OutboxState.PENDING,
        OutboxState.CLAIMED,
        OutboxState.RETRY_SCHEDULED,
        OutboxState.CONFIRMED,
    }:
        return [[InlineKeyboardButton(text=f"🔄 创建中 · {title}", callback_data=refresh)]]
    if state is OutboxState.CANCELLED:
        return []
    verb = "重试" if state is OutboxState.PRE_ACCEPT_FAILED else "获取"
    callback = f"member:invite_group:{campaign.public_ref}:{group['id']}"
    return [[InlineKeyboardButton(text=f"{verb} {title} 链接", callback_data=callback)]]


def _revoked_link_row(
    campaign: object,
    group: dict[str, Any],
    *,
    bonus_deadline: datetime | None,
) -> list[list[InlineKeyboardButton]]:
    if not _link_creation_active(campaign, bonus_deadline):
        return _missing_link_row(campaign, group, None, bonus_deadline=bonus_deadline)
    callback = f"member:invite_group:{campaign.public_ref}:{group['id']}"
    return [
        [
            InlineKeyboardButton(
                text=f"重新生成 {group['title']} 邀请入口",
                callback_data=callback,
            )
        ]
    ]


def _footer_rows(reference: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text="🔄 刷新加成状态", callback_data=f"member:bonus:{reference}")],
        [InlineKeyboardButton(text="↩ 返回活动规则", callback_data=f"member:rules:{reference}")],
    ]


def _invite_link_note(
    campaign: object,
    status: GroupInviteLinkStatus | None,
    *,
    bonus_deadline: datetime | None = None,
) -> str:
    if (
        status is not None
        and status.link is not None
        and getattr(status.link, "revoked_at", None) is not None
    ):
        return "；个人链接已撤销"
    if not campaign_link_active(campaign):
        return "；活动已截止，不再计算新增邀请加成"
    if bonus_deadline is not None and utcnow() > bonus_deadline:
        return "；最晚计奖入群时间已过，不再创建新邀请入口"
    if status is not None and status.delivery_state is OutboxState.UNKNOWN_HOLD:
        return "；链接创建结果暂时未知，不会盲目重试"
    return ""


def _page_row(reference: str, page: int, maximum: int) -> list[InlineKeyboardButton]:
    previous = max(0, page - 1)
    following = min(maximum, page + 1)
    return [
        InlineKeyboardButton(
            text="上一页", callback_data=f"member:invite_page:{reference}:{previous}"
        ),
        InlineKeyboardButton(text=f"{page + 1}/{maximum + 1}", callback_data="member:noop"),
        InlineKeyboardButton(
            text="下一页", callback_data=f"member:invite_page:{reference}:{following}"
        ),
    ]


def _invite_progress_line(title: str, rule: GroupInviteRule, progress: object) -> str:
    valid = int(getattr(progress, "valid_invites", 0))
    awarded = int(getattr(progress, "awarded_tickets", 0))
    if rule.cap_mode == "limited" and awarded >= int(rule.max_bonus_tickets or 0):
        status = f"已达上限 {awarded}/{rule.max_bonus_tickets} 次"
    else:
        remaining = rule.invites_per_cycle - valid % rule.invites_per_cycle
        status = f"已获得 {awarded} 次；距下一次还差 {remaining} 人"
    cap = "不设上限" if rule.cap_mode == "unlimited" else f"上限 {rule.max_bonus_tickets} 次"
    return f"· {title}：有效邀请 {valid} 人，{status}；{cap}"


def _activity_progress_lines(
    campaign: object,
    rules: CampaignRules,
    progress: object,
    *,
    unavailable: dict[int, str],
    visible_chat_ids: set[int],
) -> list[str]:
    all_activities = list(getattr(progress, "activities", ()))
    activities = [item for item in all_activities if item.chat_id in visible_chat_ids]
    if not activities:
        return []
    titles = _group_titles(campaign)
    total = sum(item.awarded_tickets for item in all_activities)
    lines = ["", f"今日发言加成：{total} 次"]
    for item in activities:
        rule = rules.activity_rule_for(item.chat_id)
        title = titles.get(item.chat_id, str(item.chat_id))
        if item.chat_id in unavailable:
            lines.append(f"· {title}：⚠ {unavailable[item.chat_id]}")
        elif rule is not None:
            lines.append(_activity_progress_line(title, rule, item))
    return lines


def _activity_progress_line(title: str, rule: object, progress: object) -> str:
    messages = int(progress.valid_messages)
    awarded = int(progress.awarded_tickets)
    completed = awarded // int(rule.reward_tickets)
    if rule.daily_cap_mode == "limited" and completed >= int(rule.daily_max_cycles or 0):
        status = f"已达每日上限 {completed}/{rule.daily_max_cycles} 次"
    else:
        remaining = int(rule.messages_per_cycle) - messages % int(rule.messages_per_cycle)
        status = f"已完成 {completed} 次；距下一次还差 {remaining} 条"
    cap = (
        "每日不设上限"
        if rule.daily_cap_mode == "unlimited"
        else f"每日上限 {rule.daily_max_cycles} 次"
    )
    return f"· {title}：{messages} 条，{status}；{cap}"


def _group_titles(campaign: object) -> dict[int, str]:
    presentation = getattr(campaign, "presentation", {}) or {}
    return {
        int(item["id"]): str(item.get("title") or item["id"])
        for item in presentation.get("participation_groups") or []
        if item.get("id") is not None
    }


def campaign_link_active(campaign: object) -> bool:
    return accepts_attribution(
        campaign.state,
        preheat_at=campaign.preheat_at,
        cutoff_at=campaign.cutoff_at,
        event_at=utcnow(),
    )


def _link_creation_active(campaign: object, bonus_deadline: datetime | None) -> bool:
    return campaign_link_active(campaign) and (bonus_deadline is None or utcnow() <= bonus_deadline)
