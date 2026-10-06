from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.telegram.member_record_text import participant_record_pages

TERMINAL = frozenset({CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED})


def participant_detail_view(detail, *, user_id: int, bot_username: str):
    pages = participant_record_pages(detail)
    effective = (
        str(detail.total)
        if detail.qualification is QualificationState.ELIGIBLE and detail.base_qualified
        else "待核查"
        if detail.qualification is QualificationState.UNKNOWN
        else "0"
    )
    if detail.state not in TERMINAL:
        pages = (pages[0] + f"\n\n我的进度 · 当前有效次数：{effective}", *pages[1:])
    rows = []
    if detail.base_progress:
        rows.append([_callback("查看门槛进度", f"m:gate:{detail.public_ref}")])
    if detail.bonus_enabled:
        rows.append([_callback("加成明细", f"m:bo:{detail.public_ref}")])
    if detail.invite_enabled or detail.invite_gate_enabled:
        rows.append([_callback("我的邀请链接", f"member:bonus_invites:{detail.public_ref}:0")])
        rows.append([_callback("邀请记录", f"member:invites:{detail.public_ref}:0")])
    rows.append(
        [
            _callback("查看参与条件", f"m:conditions:{detail.public_ref}:0"),
            _callback("完整规则", f"m:rules:{detail.public_ref}:0"),
        ]
    )
    if detail.state not in TERMINAL:
        rows.append(
            [
                InlineKeyboardButton(
                    text="分享活动卡片",
                    url=_activity_link(bot_username, detail.public_ref, "share"),
                )
            ]
        )
    rows.extend(_award_rows(detail, user_id=user_id, bot_username=bot_username))
    rows.extend([[_callback("返回参与列表", "nav:join")], [_callback("返回首页", "nav:index")]])
    return pages, InlineKeyboardMarkup(inline_keyboard=rows)


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
