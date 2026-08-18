from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.participant_records import ParticipatedCampaignPage
from tg_lottery.telegram.public_projection import (
    campaign_state_text,
    format_public_time,
    qualification_state_text,
)

_CATEGORY_LABELS = {
    "active": "进行中",
    "pending": "待开奖",
    "ended": "已结束",
}


def build_participated_campaign_list(
    page: ParticipatedCampaignPage, *, invitation_only: bool
) -> tuple[str, InlineKeyboardMarkup]:
    title = "👥 我的邀请记录" if invitation_only else "🎟 我参加的抽奖"
    empty = "当前分类没有可显示的邀请活动" if invitation_only else "当前分类没有参与记录"
    lines = [title, ""]
    if page.rows:
        lines.extend(_campaign_line(row) for row in page.rows)
    else:
        lines.append(empty)
    lines.extend(["", f"第 {page.page + 1}/{page.total_pages} 页"])
    return "\n".join(lines), _campaign_list_keyboard(page, invitation_only=invitation_only)


def _campaign_line(row: object) -> str:
    return (
        f"• {row.name} #{row.public_ref[:6]}｜{campaign_state_text(row.state)}｜"
        f"{row.tickets} 次｜{qualification_state_text(row.qualification)}\n"
        f"  开奖 {format_public_time(row.draw_at, row.timezone)}"
    )


def _campaign_list_keyboard(
    page: ParticipatedCampaignPage, *, invitation_only: bool
) -> InlineKeyboardMarkup:
    prefix = "nav:invites" if invitation_only else "nav:join"
    rows = [_category_row(prefix, page.category)]
    rows.extend(_campaign_button(row, invitation_only=invitation_only) for row in page.rows)
    navigation = _page_row(
        prefix,
        category=page.category,
        page=page.page,
        total_pages=page.total_pages,
    )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="返回首页", callback_data="nav:index")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _category_row(prefix: str, selected: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=("✅ " if category == selected else "") + label,
            callback_data=f"{prefix}:{category}:0",
        )
        for category, label in _CATEGORY_LABELS.items()
    ]


def _campaign_button(row: object, *, invitation_only: bool) -> list[InlineKeyboardButton]:
    callback = (
        f"member:invites:{row.public_ref}:0" if invitation_only else f"nav:joined:{row.public_ref}"
    )
    return [
        InlineKeyboardButton(
            text=f"{row.name[:24]} #{row.public_ref[:6]}｜{row.tickets} 次",
            callback_data=callback,
        )
    ]


def _page_row(
    prefix: str, *, category: str, page: int, total_pages: int
) -> list[InlineKeyboardButton]:
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(text="上一页", callback_data=f"{prefix}:{category}:{page - 1}")
        )
    if page + 1 < total_pages:
        buttons.append(
            InlineKeyboardButton(text="下一页", callback_data=f"{prefix}:{category}:{page + 1}")
        )
    return buttons


def parse_campaign_list_callback(value: str) -> tuple[str, int]:
    category, separator, raw_page = value.rpartition(":")
    if not separator or category not in _CATEGORY_LABELS or not raw_page.isdecimal():
        raise ValueError("参与记录列表按钮参数无效")
    return category, int(raw_page)
