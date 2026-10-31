from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts
from tg_lottery.application.invitation_models import accepts_attribution
from tg_lottery.application.query_invites import GroupInviteLinkStatus
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import OutboxState
from tg_lottery.domain.rules import CampaignRules, GroupInviteRule
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.member_bonus_navigation import PAGE_SIZE, footer_rows, page_row
from tg_lottery.telegram.member_bonus_text import (
    HISTORICAL_CAMPAIGN_STATES,
    bonus_header,
    campaign_bonus_completed,
    historical_outcome_status,
    invitation_section_title,
    invite_bonus_completed,
    invite_list_prompt,
)
from tg_lottery.telegram.ui_style import row_keyboard

HIDDEN_INVITE_STATES = HISTORICAL_CAMPAIGN_STATES


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
    groups = [
        group
        for group in _configured_groups(campaign, rules)
        if rules.invite_target_enabled(int(group["id"]))
    ]
    progress = await queries.group_bonus_progress(campaign.id, user_id)
    if progress is None:
        raise ValueError("group bonus progress requires an existing participant")
    invite_progress = {item.chat_id: item for item in progress.invitations}
    link_statuses = await queries.group_invite_link_statuses(campaign.id, user_id)
    outcome = await _load_outcome(campaign, queries)
    unavailable = unavailable or {}
    maximum = max(0, (len(groups) - 1) // PAGE_SIZE)
    page = min(maximum, max(0, page))
    visible = groups[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = _invite_intro(campaign, rules, outcome=outcome)
    if any(rules.invite_rule_for(int(group["id"])) for group in groups):
        lines.append("邀请加成票：")
    invite_lines, rows = _invite_section(
        campaign,
        rules,
        visible,
        invite_progress=invite_progress,
        link_statuses=link_statuses,
        unavailable=unavailable,
        cipher=cipher,
        bot_username=bot_username,
        outcome=outcome,
    )
    lines.extend(invite_lines)
    return _finish_invite_payload(campaign.public_ref, lines, rows, page=page, maximum=maximum)


async def _load_outcome(campaign: object, queries: object) -> CampaignOutcomeFacts | None:
    if campaign.state in HISTORICAL_CAMPAIGN_STATES:
        return await queries.campaign_outcome(campaign.id)
    return None


def _finish_invite_payload(
    reference: str,
    lines: list[str],
    rows: list[list[InlineKeyboardButton]],
    *,
    page: int,
    maximum: int,
) -> tuple[str, InlineKeyboardMarkup]:
    pages = [page_row(reference, page, maximum)] if maximum else []
    refresh = [
        InlineKeyboardButton(
            text="刷新邀请状态", callback_data=f"member:invite_page:{reference}:{page}"
        )
    ]
    history = [
        InlineKeyboardButton(text="查看本场邀请记录", callback_data=f"member:invites:{reference}:0")
    ]
    final_rows = [*rows, *pages, history[0:1] + refresh, *footer_rows(reference)[1:]]
    final_lines = [
        *lines,
        f"第 {page + 1}/{maximum + 1} 页",
        "",
        "链接只归因到对应群；机器人权限异常时请联系创建者修复管理员权限。",
    ]
    return "\n".join(final_lines), row_keyboard(final_rows)


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
    outcome: CampaignOutcomeFacts | None,
) -> tuple[list[str], list[list[InlineKeyboardButton]]]:
    lines = []
    rows = []
    for group in visible:
        group_lines, group_rows = _single_invite_section(
            campaign,
            rules,
            group,
            progress=invite_progress.get(int(group["id"])),
            status=link_statuses.get(int(group["id"])),
            unavailable=unavailable,
            cipher=cipher,
            bot_username=bot_username,
            outcome=outcome,
        )
        lines.extend(group_lines)
        rows.extend(group_rows)
    return lines, rows


def _single_invite_section(
    campaign: object,
    rules: CampaignRules,
    group: dict[str, Any],
    *,
    progress: object,
    status: GroupInviteLinkStatus | None,
    unavailable: dict[int, str],
    cipher: object,
    bot_username: str,
    outcome: CampaignOutcomeFacts | None,
) -> tuple[list[str], list[list[InlineKeyboardButton]]]:
    chat_id = int(group["id"])
    if chat_id in unavailable and not campaign_bonus_completed(campaign):
        lines = [f"· {group['title']}：⚠ {unavailable[chat_id]}"]
        return lines, [_permission_refresh_row(campaign.public_ref, group)]
    if not rules.invite_target_enabled(chat_id):
        return [], []
    lines = [
        _invite_group_progress(
            campaign, rules, group, progress=progress, status=status, outcome=outcome
        )
    ]
    if chat_id in unavailable:
        return [*lines, f"· {group['title']}：⚠ {unavailable[chat_id]}"], []
    deadline = campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours)
    rows = _group_rows(
        campaign, group, status, cipher=cipher, bot_username=bot_username, bonus_deadline=deadline
    )
    links = [button.copy_text.text for row in rows for button in row if button.copy_text]
    return [*lines, *links], rows


def _invite_group_progress(
    campaign: object,
    rules: CampaignRules,
    group: dict[str, Any],
    *,
    progress: object,
    status: GroupInviteLinkStatus | None,
    outcome: CampaignOutcomeFacts | None,
) -> str:
    rule = rules.invite_rule_for(int(group["id"]))
    deadline = campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours)
    if rule is None:
        gate = rules.admission.groups[str(group["id"])]
        count = progress.valid_invites if progress is not None else 0
        notice = (
            "邀请加成票关闭，以下为本期记录。"
            if campaign_bonus_completed(campaign)
            else "邀请加成票关闭，人数继续累计。"
        )
        link_note = ""
        if campaign.state not in HIDDEN_INVITE_STATES:
            link_note = _invite_link_note(
                campaign, status, bonus_deadline=deadline, outcome=outcome
            )
        return (
            f"· {group['title']}：邀请门槛 {count}/{gate.required_valid_invites} 人；"
            + notice
            + link_note
        )
    line = _invite_progress_line(
        str(group["title"]),
        rule,
        progress,
        completed=invite_bonus_completed(campaign, retention_hours=rules.invite.retention_hours),
    )
    if campaign.state in HIDDEN_INVITE_STATES:
        return line
    return line + _invite_link_note(campaign, status, bonus_deadline=deadline, outcome=outcome)


def _permission_refresh_row(reference: str, group: dict[str, Any]) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=f"🔄 重新检测 {group['title']}",
            callback_data=f"member:invite:{reference}",
        )
    ]


def _configured_groups(campaign: object, rules: CampaignRules) -> list[dict[str, Any]]:
    presentation = getattr(campaign, "presentation", {}) or {}
    records = presentation.get("participation_groups") or []
    enabled = _bonus_group_ids(rules)
    if rules.admission and rules.admission.type == "invite":
        enabled.update(map(int, rules.admission.groups))
    groups = [
        {
            "id": int(item["id"]),
            "title": chat_title(item),
            "join_url": item.get("join_url"),
        }
        for item in records
        if item.get("id") is not None and int(item["id"]) in enabled
    ]
    known = {int(item["id"]) for item in groups}
    groups.extend({"id": chat_id, "title": "群聊（名称待更新）"} for chat_id in enabled - known)
    return groups


def _bonus_group_ids(rules: CampaignRules) -> set[int]:
    invites = {int(key) for key in rules.group_invite_rules if rules.invite_rule_for(int(key))}
    activity = {int(key) for key in rules.group_activity_rules if rules.activity_rule_for(int(key))}
    return invites | activity


def _group_rows(
    campaign: object,
    group: dict[str, Any],
    status: GroupInviteLinkStatus | None,
    *,
    cipher: object,
    bot_username: str = "",
    bonus_deadline: datetime | None = None,
) -> list[list[InlineKeyboardButton]]:
    if campaign.state in HIDDEN_INVITE_STATES:
        return []
    title = str(group["title"])
    stored = status.link if status is not None else None
    if stored is None or stored.encrypted_url is None:
        return _missing_link_row(campaign, group, status, bonus_deadline=bonus_deadline)
    if getattr(stored, "revoked_at", None) is not None:
        return _revoked_link_row(campaign, group, bonus_deadline=bonus_deadline)
    if _link_expired(stored):
        return _missing_link_row(campaign, group, None, bonus_deadline=bonus_deadline)
    link = cipher.decrypt(stored.encrypted_url, context=f"invite-link:{stored.id}")
    route = status.route if status is not None else None
    referral = _referral_url(bot_username, route)
    if referral:
        return _referral_rows(
            campaign, title, referral, group_link=link, route=route, bonus_deadline=bonus_deadline
        )
    if not _link_creation_active(campaign, bonus_deadline):
        return [[InlineKeyboardButton(text=f"打开 {title}（不计加成）", url=link)]]
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
    bonus_deadline: datetime | None,
) -> list[list[InlineKeyboardButton]]:
    active = (
        _link_creation_active(campaign, bonus_deadline)
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


def _invite_link_note(
    campaign: object,
    status: GroupInviteLinkStatus | None,
    *,
    bonus_deadline: datetime | None = None,
    outcome: CampaignOutcomeFacts | None = None,
) -> str:
    link_note = _unavailable_link_note(status)
    historical_status = historical_outcome_status(campaign, outcome=outcome)
    if historical_status is not None:
        return _completed_invite_note(link_note, status=historical_status)
    if campaign_bonus_completed(campaign):
        return "；活动已截止，不再计算新增邀请加成票" + link_note
    if link_note:
        return link_note
    if utcnow() < campaign.preheat_at:
        return "；邀请加成票尚未开始"
    if bonus_deadline is not None and utcnow() > bonus_deadline:
        return "；最晚计奖入群时间已过；仅核验此前邀请，不再创建新邀请入口"
    if status is not None and status.delivery_state is OutboxState.UNKNOWN_HOLD:
        return "；链接创建结果暂时未知，不会盲目重试"
    return ""


def _completed_invite_note(link_note: str, *, status: str) -> str:
    notice = f"；{status}，邀请入口已隐藏，不再计算新增邀请加成票"
    if link_note:
        return notice + link_note
    return notice + "；仍有效的旧链接可入群"


def _invite_progress_line(
    title: str, rule: GroupInviteRule, progress: object, *, completed: bool = False
) -> str:
    valid = int(getattr(progress, "valid_invites", 0))
    awarded = int(getattr(progress, "awarded_tickets", 0))
    if rule is None:
        return f"· {title}：有效邀请 {valid} 人，邀请加成票关闭；门槛进度见下方。"
    if rule.cap_mode == "limited" and awarded >= int(rule.max_bonus_tickets or 0):
        status = f"已达上限 {awarded}/{rule.max_bonus_tickets} 次"
    elif completed:
        status = f"已获得 {awarded} 次"
    else:
        remaining = rule.invites_per_cycle - valid % rule.invites_per_cycle
        status = f"已获得 {awarded} 次；距下一次还差 {remaining} 人"
    cap = "不设上限" if rule.cap_mode == "unlimited" else f"上限 {rule.max_bonus_tickets} 次"
    return f"· {title}：有效邀请 {valid} 人，{status}；{cap}"


def _activity_progress_line(
    title: str, rule: object, progress: object, *, completed: bool = False
) -> str:
    messages = int(progress.valid_messages)
    awarded = int(progress.awarded_tickets)
    total = progress.total_awarded_tickets
    days = progress.qualified_days
    if completed:
        return f"· {title}：累计达标 {days} 天、获得 {total} 次"
    completed_cycles = awarded // int(rule.reward_tickets)
    if rule.daily_cap_mode == "limited" and completed_cycles >= int(rule.daily_max_cycles or 0):
        status = f"已达每日上限 {completed_cycles}/{rule.daily_max_cycles} 次"
    else:
        remaining = int(rule.messages_per_cycle) - messages % int(rule.messages_per_cycle)
        status = f"已完成 {completed_cycles} 次；距下一次还差 {remaining} 条"
    cap = (
        "每日不设上限"
        if rule.daily_cap_mode == "unlimited"
        else f"每日上限 {rule.daily_max_cycles} 次"
    )
    return f"· {title}：今日 {messages} 条，{status}；{cap}；累计达标 {days} 天、获得 {total} 次"


def _group_titles(campaign: object) -> dict[int, str]:
    presentation = getattr(campaign, "presentation", {}) or {}
    return {
        int(item["id"]): chat_title(item)
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


def _link_expired(link: object) -> bool:
    expires = getattr(link, "expire_at", None)
    return expires is not None and expires <= utcnow()


def _unavailable_link_note(status: GroupInviteLinkStatus | None) -> str:
    if status is None or status.link is None:
        return ""
    if getattr(status.link, "revoked_at", None) is not None:
        return "；个人链接已撤销"
    if _link_expired(status.link):
        return "；旧版邀请链接已过期，请使用可用的普通入群入口"
    return ""


def _invite_intro(campaign, rules, *, outcome):
    return [
        bonus_header(
            campaign,
            "历史邀请记录"
            if campaign_bonus_completed(campaign)
            else invitation_section_title(rules) + "入口",
            outcome=outcome,
        ),
        invite_list_prompt(campaign, retention_hours=rules.invite.retention_hours, outcome=outcome),
        f"有效邀请：入群满 {rules.invite.retention_hours} 小时后核验；同群同人只算一次。",
    ]
