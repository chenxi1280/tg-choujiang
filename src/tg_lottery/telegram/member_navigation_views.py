from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.keyboard_layout import columns
from tg_lottery.telegram.member_detail_actions import (
    REGISTRATION_STATES,
    detail_task,
    invitation_task_available,
)
from tg_lottery.telegram.member_record_text import current_effective_count, participant_record_pages
from tg_lottery.telegram.operational_issue_views import operational_issue_text
from tg_lottery.telegram.public_projection import format_public_time
from tg_lottery.telegram.ui_style import style_keyboard

TERMINAL = frozenset({CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED})


def participant_detail_view(detail, *, user_id: int, bot_username: str):
    pages = participant_record_pages(detail)
    now = utcnow()
    pages, primary = _detail_body(detail, pages=pages, now=now)
    rows = [[primary]] if primary is not None else []
    rows.extend(_progress_rows(detail, now))
    rows.append(
        [
            _callback("查看参与条件", f"m:conditions:{detail.public_ref}:0"),
            _callback("完整规则", f"m:rules:{detail.public_ref}:0"),
        ]
    )
    if detail.state in REGISTRATION_STATES and (
        detail.cutoff_at is None or now <= detail.cutoff_at
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text="分享活动卡片",
                    url=_activity_link(bot_username, detail.public_ref, "share"),
                )
            ]
        )
    rows.extend(_award_rows(detail, user_id=user_id, bot_username=bot_username))
    rows.append([_callback("返回参与列表", "nav:join"), _callback("返回首页", "nav:index")])
    return pages, style_keyboard(InlineKeyboardMarkup(inline_keyboard=_distinct_rows(rows)))


def _detail_body(detail, *, pages, now):
    effective = current_effective_count(detail)
    if detail.state not in TERMINAL:
        pages = (pages[0] + f"\n\n我的进度 · 当前有效次数：{effective}", *pages[1:])
    notice, primary = detail_task(detail, now)
    if detail.cutoff_at is not None:
        cutoff = format_public_time(detail.cutoff_at, detail.timezone, seconds=True)
        notice = f"报名/行为统计截止：{cutoff}\n" + notice
    if detail.operational_issues:
        notice += "\n" + operational_issue_text(detail.operational_issues)
    if notice:
        pages = (pages[0] + "\n\n" + notice, *pages[1:])
    return pages, primary


def _progress_rows(detail, now):
    rows = []
    if detail.base_progress:
        rows.append([_callback("查看门槛进度", f"m:gate:{detail.public_ref}")])
    if detail.bonus_enabled:
        rows.append([_callback("加成明细", f"m:bo:{detail.public_ref}")])
    if detail.invite_enabled or detail.invite_gate_enabled:
        if invitation_task_available(detail, now):
            rows.append([_callback("我的邀请链接", f"member:bonus_invites:{detail.public_ref}:0")])
        records = _callback("邀请记录", f"member:invites:{detail.public_ref}:0")
        rows.append([records])
    return columns([button for row in rows for button in row])


def _distinct_rows(rows):
    seen, result = set(), []
    for row in rows:
        selected = []
        for button in row:
            identity = button.callback_data or button.url or button.text
            if identity not in seen:
                seen.add(identity)
                selected.append(button)
        if selected:
            result.append(selected)
    return result


def _award_rows(detail, *, user_id: int, bot_username: str) -> list:
    result = detail.draw_result
    if result is None or not result.awards:
        return []
    rows = [
        [InlineKeyboardButton(text="联系发奖人员", url=f"tg://user?id={result.issuer_user_id}")]
    ]
    for award in result.awards:
        text = (
            f"活动：{_activity_link(bot_username, detail.public_ref, 'join')}\n"
            f"本人ID：{user_id}\n奖品第{award.prize_position}项，名额{award.prize_slot_no}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"复制第{award.prize_position}项领奖联系信息",
                    copy_text=CopyTextButton(text=text),
                )
            ]
        )
    return rows


def _activity_link(bot_username: str, reference: str, kind: str) -> str:
    return f"https://t.me/{bot_username}?start={kind}_{reference}"


def _callback(label: str, route: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=route)
