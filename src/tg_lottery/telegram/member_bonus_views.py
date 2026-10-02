from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.query_invites import GroupInviteLinkStatus
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_bonus_text import (
    activity_cap as _activity_cap,
)
from tg_lottery.telegram.member_bonus_text import (
    bonus_closed_notice,
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
    PAGE_SIZE,
    _activity_progress_line,
    _configured_groups,
    _group_rows,
    _invite_link_note,
    _invite_progress_line,
    campaign_bonus_completed,
)
from tg_lottery.telegram.public_projection import format_public_time


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
    has_invites = any(rule.enabled for rule in context.rules.group_invite_rules.values())
    has_activity = any(rule.enabled for rule in context.rules.group_activity_rules.values())
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
    )


def _groups_with_join_urls(
    groups: tuple[dict[str, object], ...], *, targets: tuple[object, ...], cipher: object
) -> tuple[dict[str, object], ...]:
    by_chat = {target.chat_id: target for target in targets if target.kind == "participation_group"}
    return tuple(
        group
        | {
            "join_url": _target_join_url(by_chat.get(int(group["id"])), cipher)
            or group.get("join_url")
        }
        for group in groups
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
            _header(context.campaign, "我的加成"),
            bonus_closed_notice(context.campaign),
            context.base_progress,
        ]
    )
    ref = context.campaign.public_ref
    activity_label = (
        f"💬 发言加成｜累计 {cumulative} 次"
        if campaign_bonus_completed(context.campaign)
        else f"💬 发言加成｜今日 {activity_total} 次 · 累计 {cumulative} 次"
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"👥 邀请加成｜{invite_total} 次",
                callback_data=f"member:bonus_invites:{ref}:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=activity_label,
                callback_data=f"member:bonus_activity:{ref}:0",
            )
        ],
        [InlineKeyboardButton(text="🎟 抽奖次数明细", callback_data=f"member:tickets:{ref}")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def build_invite_group_list(
    context: BonusViewContext, *, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    groups = tuple(
        group
        for group in context.groups
        if context.rules.invite_rule_for(int(group["id"])) is not None
    )
    visible, selected, total_pages = _visible_page(groups, page)
    lines = [
        _header(context.campaign, "邀请加成"),
        invite_list_prompt(context.campaign, retention_hours=context.rules.invite.retention_hours),
    ]
    completed = invite_bonus_completed(
        context.campaign, retention_hours=context.rules.invite.retention_hours
    )
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
    _append_navigation(
        rows,
        reference=context.campaign.public_ref,
        category="invites",
        page=selected,
        total_pages=total_pages,
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="返回我的加成", callback_data=f"m:bo:{context.campaign.public_ref}"
            )
        ]
    )
    return "\n".join([*lines, context.base_progress]), InlineKeyboardMarkup(inline_keyboard=rows)


def build_activity_group_list(
    context: BonusViewContext, *, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    groups = tuple(
        group
        for group in context.groups
        if context.rules.activity_rule_for(int(group["id"])) is not None
    )
    visible, selected, total_pages = _visible_page(groups, page)
    lines = [_header(context.campaign, "发言加成"), "请选择一个群查看今日进度和累计加成："]
    completed = campaign_bonus_completed(context.campaign)
    if completed:
        lines[1] = bonus_closed_notice(context.campaign, category="发言")
    rows = []
    for group in visible:
        chat_id = int(group["id"])
        rule = context.rules.activity_rule_for(chat_id)
        progress = context.activity_progress.get(chat_id)
        if rule is None or progress is None:
            continue
        lines.append(
            _activity_progress_line(
                str(group["title"]),
                rule,
                progress,
                completed=completed,
            )
        )
        rows.append(
            _group_detail_row(context.campaign.public_ref, "activity", group, group_page=selected)
        )
    _append_navigation(
        rows,
        reference=context.campaign.public_ref,
        category="activity",
        page=selected,
        total_pages=total_pages,
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="返回我的加成", callback_data=f"m:bo:{context.campaign.public_ref}"
            )
        ]
    )
    return "\n".join([*lines, context.base_progress]), InlineKeyboardMarkup(inline_keyboard=rows)


def _invite_group_detail(
    context: BonusViewContext, chat_id: int, *, return_page: int
) -> tuple[str, InlineKeyboardMarkup]:
    group = _required_group(context, chat_id)
    rule = context.rules.invite_rule_for(chat_id)
    if rule is None:
        raise ValueError("该群未开启邀请加成")
    status = context.link_statuses.get(chat_id)
    progress = context.invite_progress.get(chat_id)
    deadline = context.campaign.cutoff_at - timedelta(hours=context.rules.invite.retention_hours)
    completed = invite_bonus_completed(
        context.campaign, retention_hours=context.rules.invite.retention_hours
    )
    lines = [
        _header(context.campaign, f"{group['title']} 邀请加成"),
        f"规则：每 {rule.invites_per_cycle} 名有效新成员 +1；{_invite_cap(rule)}",
        f"最晚计奖入群：{format_public_time(deadline, context.campaign.timezone)}",
        _invite_progress_line(str(group["title"]), rule, progress, completed=completed),
        _invite_link_note(context.campaign, status, bonus_deadline=deadline).removeprefix("；"),
    ]
    rows = _available_invite_rows(
        context,
        group,
        status,
        deadline=deadline,
        return_page=return_page,
    )
    rows.extend(_invite_detail_navigation(context, chat_id, return_page=return_page))
    return "\n".join(
        line for line in [*lines, context.base_progress] if line
    ), InlineKeyboardMarkup(inline_keyboard=rows)


def _invite_detail_navigation(
    context: BonusViewContext, chat_id: int, *, return_page: int
) -> list[list[InlineKeyboardButton]]:
    reference = context.campaign.public_ref
    return [
        [
            InlineKeyboardButton(
                text="邀请记录",
                callback_data=f"m:ir:{reference}:{chat_id}:{return_page}:all:0",
            )
        ],
        [
            InlineKeyboardButton(
                text="刷新本群状态",
                callback_data=f"m:id:{reference}:{chat_id}:{return_page}",
            )
        ],
        [
            InlineKeyboardButton(
                text="返回邀请群列表",
                callback_data=f"member:bonus_invites:{reference}:{return_page}",
            )
        ],
    ]


def _activity_group_detail(
    context: BonusViewContext, chat_id: int, *, return_page: int
) -> tuple[str, InlineKeyboardMarkup]:
    group = _required_group(context, chat_id)
    rule = context.rules.activity_rule_for(chat_id)
    progress = context.activity_progress.get(chat_id)
    if rule is None or progress is None:
        raise ValueError("该群未开启发言加成")
    completed = campaign_bonus_completed(context.campaign)
    lines = [
        _header(context.campaign, f"{group['title']} 发言加成"),
        f"规则：每 {rule.messages_per_cycle} 条有效消息 +{rule.reward_tickets} 次",
        f"每条至少 {rule.min_text_length} 个可计数字符；{_activity_cap(rule)}",
        _activity_progress_line(str(group["title"]), rule, progress, completed=completed),
        bonus_closed_notice(context.campaign, category="发言"),
    ]
    if chat_id in context.unavailable:
        lines.append(f"⚠ {context.unavailable[chat_id]}")
    rows = [
        [
            InlineKeyboardButton(
                text="刷新发言进度",
                callback_data=f"m:ad:{context.campaign.public_ref}:{chat_id}:{return_page}",
            )
        ],
        [
            InlineKeyboardButton(
                text="返回发言群列表",
                callback_data=(
                    f"member:bonus_activity:{context.campaign.public_ref}:{return_page}"
                ),
            )
        ],
    ]
    return "\n".join([*lines, context.base_progress]), InlineKeyboardMarkup(inline_keyboard=rows)


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


def _required_group(context: BonusViewContext, chat_id: int) -> dict[str, object]:
    group = next((item for item in context.groups if int(item["id"]) == chat_id), None)
    if group is None:
        raise ValueError("参与群不存在或已移除")
    return group


def _group_detail_row(
    reference: str, category: str, group: dict[str, object], *, group_page: int
) -> list[InlineKeyboardButton]:
    callback = "id" if category == "invites" else "ad"
    data = f"m:{callback}:{reference}:{group['id']}:{group_page}"
    return [InlineKeyboardButton(text=str(group["title"]), callback_data=data)]


def _visible_page(
    groups: tuple[dict[str, object], ...], page: int
) -> tuple[tuple[dict[str, object], ...], int, int]:
    total_pages = max(1, (len(groups) + PAGE_SIZE - 1) // PAGE_SIZE)
    selected = min(max(0, page), total_pages - 1)
    start = selected * PAGE_SIZE
    return groups[start : start + PAGE_SIZE], selected, total_pages


def _append_navigation(
    rows: list[list[InlineKeyboardButton]],
    *,
    reference: str,
    category: str,
    page: int,
    total_pages: int,
) -> None:
    prefix = "bonus_invites" if category == "invites" else "bonus_activity"
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text="上一页", callback_data=f"member:{prefix}:{reference}:{page - 1}"
            )
        )
    if page + 1 < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="下一页", callback_data=f"member:{prefix}:{reference}:{page + 1}"
            )
        )
    if buttons:
        rows.append(buttons)
