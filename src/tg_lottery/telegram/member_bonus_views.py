from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts
from tg_lottery.application.query_invites import GroupInviteLinkStatus
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.target_identity import distinct_group_names
from tg_lottery.telegram.keyboard_layout import list_rows
from tg_lottery.telegram.member_bonus_navigation import (
    PAGE_SIZE,
    navigation_rows,
)
from tg_lottery.telegram.member_bonus_navigation import (
    group_detail_row as _group_detail_row,
)
from tg_lottery.telegram.member_bonus_navigation import (
    visible_page as _visible_page,
)
from tg_lottery.telegram.member_bonus_task_views import (
    activity_group_detail as _activity_group_detail,
)
from tg_lottery.telegram.member_bonus_task_views import (
    invite_detail_navigation as _invite_detail_navigation,
)
from tg_lottery.telegram.member_bonus_task_views import (
    required_group as _required_group,
)
from tg_lottery.telegram.member_bonus_text import (
    HISTORICAL_CAMPAIGN_STATES,
    bonus_closed_notice,
    invitation_section_title,
    invite_bonus_completed,
    invite_list_prompt,
)
from tg_lottery.telegram.member_bonus_text import (
    bonus_header as _header,
)
from tg_lottery.telegram.member_bonus_text import (
    invite_cap as _invite_cap,
)
from tg_lottery.telegram.member_group_invites import (
    HIDDEN_INVITE_STATES,
    _activity_progress_line,
    _configured_groups,
    _group_rows,
    _invite_link_note,
    _invite_progress_line,
    campaign_bonus_completed,
)
from tg_lottery.telegram.public_projection import format_public_time
from tg_lottery.telegram.ui_style import row_keyboard, style_keyboard


@dataclass(frozen=True)
class BonusViewContext:
    campaign: object
    rules: CampaignRules
    groups: tuple[dict[str, object], ...]
    invite_progress: Mapping[int, object]
    activity_progress: Mapping[int, object]
    link_statuses: Mapping[int, GroupInviteLinkStatus]
    unavailable: Mapping[int, str]
    cipher: object
    bot_username: str
    outcome: CampaignOutcomeFacts | None = None
    base_progress: str = ""


async def build_bonus_overview(
    campaign: object,
    *,
    user_id: int,
    queries: object,
    cipher: object,
    bot_username: str,
    unavailable: Mapping[int, str],
) -> tuple[str, InlineKeyboardMarkup]:
    context = await _load_context(
        campaign,
        user_id=user_id,
        queries=queries,
        cipher=cipher,
        bot_username=bot_username,
        unavailable=unavailable,
    )
    has_invites = any(
        context.rules.invite_target_enabled(int(group["id"])) for group in context.groups
    )
    has_activity = any(
        context.rules.activity_rule_for(int(group["id"])) for group in context.groups
    )
    if not has_invites and not has_activity:
        raise UserFacingError("本期抽奖没有可展示的逐群加成")
    if has_invites and not has_activity:
        return build_invite_group_list(context, page=0)
    if has_activity and not has_invites:
        return build_activity_group_list(context, page=0)
    return _category_payload(context)


async def build_bonus_category(
    campaign: object,
    *,
    user_id: int,
    category: str,
    page: int,
    queries: object,
    cipher: object,
    bot_username: str,
    unavailable: Mapping[int, str],
) -> tuple[str, InlineKeyboardMarkup]:
    context = await _load_context(
        campaign,
        user_id=user_id,
        queries=queries,
        cipher=cipher,
        bot_username=bot_username,
        unavailable=unavailable,
    )
    if category == "invites":
        return build_invite_group_list(context, page=page)
    if category == "activity":
        return build_activity_group_list(context, page=page)
    raise ValueError("加成分类无效")


async def build_bonus_group_detail(
    campaign: object,
    *,
    user_id: int,
    category: str,
    chat_id: int,
    return_page: int,
    queries: object,
    cipher: object,
    bot_username: str,
    unavailable: Mapping[int, str],
) -> tuple[str, InlineKeyboardMarkup]:
    context = await _load_context(
        campaign,
        user_id=user_id,
        queries=queries,
        cipher=cipher,
        bot_username=bot_username,
        unavailable=unavailable,
    )
    if category == "invites":
        return _invite_group_detail(context, chat_id, return_page=return_page)
    if category == "activity":
        return _activity_group_detail(context, chat_id, return_page=return_page)
    raise ValueError("加成分类无效")


async def _load_context(
    campaign: object,
    *,
    user_id: int,
    queries: object,
    cipher: object,
    bot_username: str,
    unavailable: Mapping[int, str],
) -> BonusViewContext:
    rules = CampaignRules.model_validate(campaign.rules)
    progress = await queries.group_bonus_progress(campaign.id, user_id)
    if progress is None:
        raise ValueError("group bonus progress requires an existing participant")
    statuses = await queries.group_invite_link_statuses(campaign.id, user_id)
    outcome = (
        await queries.campaign_outcome(campaign.id)
        if campaign.state in HISTORICAL_CAMPAIGN_STATES
        else None
    )
    targets = await queries.qualification_targets(campaign.id)
    groups = _groups_with_join_urls(
        tuple(_configured_groups(campaign, rules)),
        targets=targets,
        cipher=cipher,
    )
    return BonusViewContext(
        campaign=campaign,
        rules=rules,
        groups=groups,
        base_progress=progress.base_progress,
        invite_progress={item.chat_id: item for item in progress.invitations},
        activity_progress={item.chat_id: item for item in progress.activities},
        link_statuses=statuses,
        unavailable=unavailable,
        cipher=cipher,
        bot_username=bot_username,
        outcome=outcome,
    )


def _groups_with_join_urls(
    groups: tuple[dict[str, object], ...], *, targets: tuple[object, ...], cipher: object
) -> tuple[dict[str, object], ...]:
    by_chat = {
        getattr(target, "rule_chat_id", None) or target.chat_id: target
        for target in targets
        if target.kind == "participation_group"
    }
    return distinct_group_names(
        tuple(
            group
            | {
                "join_url": _target_join_url(by_chat.get(int(group["id"])), cipher)
                or group.get("join_url")
            }
            for group in groups
        )
    )


def _target_join_url(target: object | None, cipher: object) -> str | None:
    if target is None:
        return None
    if target.public_join_url:
        return str(target.public_join_url)
    if target.revoked_at is not None:
        return None
    if target.encrypted_join_url is None:
        return None
    return cipher.decrypt(target.encrypted_join_url, context=target.secret_context)


def _category_payload(context: BonusViewContext) -> tuple[str, InlineKeyboardMarkup]:
    invite_total = sum(item.awarded_tickets for item in context.invite_progress.values())
    activity_total = sum(item.awarded_tickets for item in context.activity_progress.values())
    cumulative = sum(item.total_awarded_tickets for item in context.activity_progress.values())
    text = "\n".join(
        [
            _header(context.campaign, "我的加成", outcome=context.outcome),
            bonus_closed_notice(context.campaign, outcome=context.outcome),
            context.base_progress,
        ]
    )
    ref = context.campaign.public_ref
    activity_label = (
        f"🌱 活跃加成（人品）｜累计 {cumulative} 次"
        if campaign_bonus_completed(context.campaign)
        else f"🌱 活跃加成（人品）｜今日 {activity_total} 次 · 累计 {cumulative} 次"
    )
    rows = [
        [
            InlineKeyboardButton(
                text="🤝 " + invitation_section_title(context.rules),
                callback_data=f"member:bonus_invites:{ref}:0",
            )
        ],
        [
            InlineKeyboardButton(
                text="🌱 活跃加成（人品）",
                callback_data=f"member:bonus_activity:{ref}:0",
            )
        ],
        [InlineKeyboardButton(text="返回活动详情", callback_data=f"member:tickets:{ref}")],
        [InlineKeyboardButton(text="返回首页", callback_data="nav:index")],
    ]
    text += f"\n\n邀请累计：{invite_total} 次\n{activity_label}"
    return text, style_keyboard(
        InlineKeyboardMarkup(inline_keyboard=[rows[0] + rows[1], rows[2] + rows[3]])
    )


def build_invite_group_list(
    context: BonusViewContext, *, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    groups = tuple(
        group for group in context.groups if context.rules.invite_target_enabled(int(group["id"]))
    )
    if len(groups) == 1:
        return _invite_group_detail(context, int(groups[0]["id"]), return_page=0)
    visible, selected, total_pages = _visible_page(groups, page)
    lines = [
        _header(context.campaign, invitation_section_title(context.rules), outcome=context.outcome),
        invite_list_prompt(
            context.campaign,
            retention_hours=context.rules.invite.retention_hours,
            outcome=context.outcome,
        ),
    ]
    completed = _invite_bonus_completed(context)
    rows = []
    for group in visible:
        chat_id = int(group["id"])
        rule = context.rules.invite_rule_for(chat_id)
        lines.append(
            _invite_progress_line(
                str(group["title"]),
                rule,
                context.invite_progress.get(chat_id),
                completed=completed,
            )
        )
        rows.append(
            _group_detail_row(context.campaign.public_ref, "invites", group, group_page=selected)
        )
    rows = list_rows(rows, start=selected * PAGE_SIZE + 1)
    rows.extend(
        navigation_rows(
            reference=context.campaign.public_ref,
            category="invites",
            page=selected,
            total_pages=total_pages,
        )
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="返回活动详情", callback_data=f"member:tickets:{context.campaign.public_ref}"
            )
        ]
    )
    return "\n".join([*lines, context.base_progress]), row_keyboard(rows)


def build_activity_group_list(
    context: BonusViewContext, *, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    groups = tuple(
        group
        for group in context.groups
        if context.rules.activity_rule_for(int(group["id"])) is not None
    )
    if len(groups) == 1:
        return _activity_group_detail(context, int(groups[0]["id"]), return_page=0)
    visible, selected, total_pages = _visible_page(groups, page)
    lines = [
        _header(context.campaign, "活跃加成（人品）", outcome=context.outcome),
        "请选择一个群查看今日进度和累计加成：",
    ]
    completed = campaign_bonus_completed(context.campaign)
    if completed:
        lines[1] = bonus_closed_notice(context.campaign, category="发言", outcome=context.outcome)
    rows = []
    for group in visible:
        lines.append(_activity_list_line(context, group, completed=completed))
        rows.append(
            _group_detail_row(context.campaign.public_ref, "activity", group, group_page=selected)
        )
    rows = list_rows(rows, start=selected * PAGE_SIZE + 1)
    rows.extend(
        navigation_rows(
            reference=context.campaign.public_ref,
            category="activity",
            page=selected,
            total_pages=total_pages,
        )
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="返回活动详情", callback_data=f"member:tickets:{context.campaign.public_ref}"
            )
        ]
    )
    return "\n".join([*lines, context.base_progress]), style_keyboard(
        InlineKeyboardMarkup(inline_keyboard=rows)
    )


def _invite_group_detail(
    context: BonusViewContext, chat_id: int, *, return_page: int
) -> tuple[str, InlineKeyboardMarkup]:
    group = _required_group(context, chat_id)
    rule = context.rules.invite_rule_for(chat_id)
    if not context.rules.invite_target_enabled(chat_id):
        raise ValueError("该群未开启邀请门槛或加成")
    status = context.link_statuses.get(chat_id)
    progress = context.invite_progress.get(chat_id)
    deadline = context.campaign.cutoff_at - timedelta(hours=context.rules.invite.retention_hours)
    completed = _invite_bonus_completed(context)
    lines = [
        _header(
            context.campaign,
            f"{group['title']} {invitation_section_title(context.rules)}",
            outcome=context.outcome,
        ),
        _invite_rule_text(rule),
        f"最晚计奖入群：{format_public_time(deadline, context.campaign.timezone)}",
        _invite_progress_line(str(group["title"]), rule, progress, completed=completed),
        _invite_link_note(
            context.campaign, status, bonus_deadline=deadline, outcome=context.outcome
        ).removeprefix("；"),
    ]
    rows = _available_invite_rows(
        context,
        group,
        status,
        deadline=deadline,
        return_page=return_page,
    )
    rows.extend(_invite_detail_navigation(context, chat_id, return_page=return_page))
    return "\n".join(line for line in [*lines, context.base_progress] if line), style_keyboard(
        InlineKeyboardMarkup(inline_keyboard=rows)
    )


def _available_invite_rows(
    context: BonusViewContext,
    group: dict[str, object],
    status: GroupInviteLinkStatus | None,
    *,
    deadline: datetime,
    return_page: int,
) -> list[list[InlineKeyboardButton]]:
    if context.campaign.state in HIDDEN_INVITE_STATES:
        return []
    chat_id = int(group["id"])
    if chat_id in context.unavailable:
        return [
            [
                InlineKeyboardButton(
                    text="重新检测群权限",
                    callback_data=f"m:id:{context.campaign.public_ref}:{chat_id}:{return_page}",
                )
            ]
        ]
    return _group_rows(
        context.campaign,
        group,
        status,
        cipher=context.cipher,
        bot_username=context.bot_username,
        bonus_deadline=deadline,
    )


def _invite_bonus_completed(context: BonusViewContext) -> bool:
    return invite_bonus_completed(
        context.campaign, retention_hours=context.rules.invite.retention_hours
    )


def _invite_rule_text(rule):
    if rule is None:
        return "邀请加成关闭; 有效邀请仍用于本场门槛。"
    return f"规则：每 {rule.invites_per_cycle} 名有效新成员 +1；{_invite_cap(rule)}"


def _activity_list_line(context, group: dict, *, completed: bool) -> str:
    chat_id = int(group["id"])
    rule = context.rules.activity_rule_for(chat_id)
    progress = context.activity_progress.get(chat_id)
    if progress is None:
        return str(group["title"]) + "：当前进度待核查，不能视为0条或已完成"
    return _activity_progress_line(str(group["title"]), rule, progress, completed=completed)
