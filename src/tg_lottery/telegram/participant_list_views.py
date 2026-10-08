from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.participant_records import (
    PARTICIPATED_CAMPAIGN_PAGE_SIZE,
    ParticipatedCampaignPage,
)
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.keyboard_layout import columns, list_rows
from tg_lottery.telegram.public_projection import (
    campaign_history_state_text,
    format_public_time,
    qualification_state_text,
)
from tg_lottery.telegram.ui_style import style_keyboard

_LIST_ROUTE_FIELDS = 2
_LIST_ROUTE_WITH_PARENT_FIELDS = 4

_CATEGORY_LABELS = {
    "active": "进行中",
    "pending": "待开奖",
    "ended": "已结束",
}


def build_participated_campaign_list(
    page: ParticipatedCampaignPage, *, invitation_only: bool, parent: tuple[str, int] | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    title = "🤝 我的邀请记录" if invitation_only else "✨ 我参加的抽奖"
    empty = "当前分类没有可显示的邀请活动" if invitation_only else "当前分类没有参与记录"
    lines = [title, ""]
    if page.rows:
        lines.extend(
            _campaign_line(row, position=position)
            for position, row in enumerate(
                page.rows, page.page * PARTICIPATED_CAMPAIGN_PAGE_SIZE + 1
            )
        )
    else:
        lines.append(empty)
    lines.extend(["", f"第 {page.page + 1}/{page.total_pages} 页"])
    return "\n".join(lines), _campaign_list_keyboard(
        page, invitation_only=invitation_only, parent=parent
    )


def _campaign_line(row: object, *, position: int) -> str:
    return (
        f"{position}. {row.name}｜{_state_label(row)}｜"
        f"{_ticket_label(row)}｜{qualification_state_text(row.qualification)}\n"
        f"  开奖 {format_public_time(row.draw_at, row.timezone)}"
    )


def _campaign_list_keyboard(
    page: ParticipatedCampaignPage, *, invitation_only: bool, parent: tuple[str, int] | None = None
) -> InlineKeyboardMarkup:
    prefix = "nav:invites" if invitation_only else "nav:join"
    rows = columns(_category_row(prefix, page.category, parent=parent))
    category_rows = len(rows)
    rows.extend(
        _campaign_button(
            row, source=page, invitation_only=invitation_only, position=position, parent=parent
        )
        for position, row in enumerate(page.rows, page.page * PARTICIPATED_CAMPAIGN_PAGE_SIZE + 1)
    )
    rows = [
        *rows[:category_rows],
        *list_rows(rows[category_rows:], start=page.page * PARTICIPATED_CAMPAIGN_PAGE_SIZE + 1),
    ]
    navigation = _page_row(
        prefix,
        category=page.category,
        page=page.page,
        total_pages=page.total_pages,
        parent=parent,
    )
    if navigation:
        rows.append(navigation)
    if not invitation_only:
        route = f"nav:invites:{page.category}:0:{page.category}:{page.page}"
        rows.append([InlineKeyboardButton(text="我的邀请记录", callback_data=route)])
    elif parent is not None:
        route = f"nav:join:{parent[0]}:{parent[1]}"
        rows.append([InlineKeyboardButton(text="返回参与列表", callback_data=route)])
    rows.append([InlineKeyboardButton(text="返回首页", callback_data="nav:index")])
    return style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows))


def _category_row(
    prefix: str, selected: str, *, parent: tuple[str, int] | None
) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=("✅ " if category == selected else "") + label,
            callback_data=f"{prefix}:{category}:0" + _parent_suffix(parent),
        )
        for category, label in _CATEGORY_LABELS.items()
    ]


def _campaign_button(
    row: object,
    *,
    source: ParticipatedCampaignPage,
    invitation_only: bool,
    position: int,
    parent: tuple[str, int] | None,
) -> list[InlineKeyboardButton]:
    callback = (
        f"member:invites:{row.public_ref}:0:{source.category}:{source.page}"
        + _parent_suffix(parent)
        if invitation_only
        else f"m:detail:{row.public_ref}:{source.category}:{source.page}"
    )
    return [
        InlineKeyboardButton(
            text=f"{row.name}｜{_ticket_label(row)}",
            callback_data=callback,
        )
    ]


def _page_row(
    prefix: str, *, category: str, page: int, total_pages: int, parent: tuple[str, int] | None
) -> list[InlineKeyboardButton]:
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text="上一页",
                callback_data=f"{prefix}:{category}:{page - 1}" + _parent_suffix(parent),
            )
        )
    if page + 1 < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="下一页",
                callback_data=f"{prefix}:{category}:{page + 1}" + _parent_suffix(parent),
            )
        )
    return buttons


def parse_campaign_list_callback(value: str) -> tuple[str, int]:
    category, separator, raw_page = value.rpartition(":")
    if not separator or category not in _CATEGORY_LABELS or not raw_page.isdecimal():
        raise ValueError("参与记录列表按钮参数无效")
    return category, int(raw_page)


def parse_invitation_list_callback(value: str) -> tuple[str, int, tuple[str, int] | None]:
    parts = value.split(":")
    if len(parts) not in {_LIST_ROUTE_FIELDS, _LIST_ROUTE_WITH_PARENT_FIELDS}:
        raise ValueError("邀请活动列表按钮参数无效")
    category, page = parse_campaign_list_callback(":".join(parts[:_LIST_ROUTE_FIELDS]))
    parent = (
        parse_campaign_list_callback(":".join(parts[_LIST_ROUTE_FIELDS:]))
        if len(parts) == _LIST_ROUTE_WITH_PARENT_FIELDS
        else None
    )
    return category, page, parent


def _parent_suffix(parent: tuple[str, int] | None) -> str:
    return f":{parent[0]}:{parent[1]}" if parent is not None else ""


def _state_label(row: object) -> str:
    return campaign_history_state_text(row.state, row.outcome_kind)


def _ticket_label(row: object) -> str:
    if row.state in {CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED}:
        return _historical_ticket_label(row)
    if not row.base_qualified:
        return f"门槛未达标 · 累计 {row.tickets} 次（暂存）"
    return f"{row.tickets} 次"


def _historical_ticket_label(row: object) -> str:
    if row.outcome_kind == "cancelled":
        return f"历史累计 {row.tickets} 次 · 活动已取消，未开奖"
    if row.outcome_kind == "drawn":
        status = "未计入本场" if not row.base_qualified else "最终次数见详情"
        return f"历史累计 {row.tickets} 次 · {status}"
    return f"历史累计 {row.tickets} 次 · 历史结果未确认，待核查"
