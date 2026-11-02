from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.campaign_targets import QualificationTarget
from tg_lottery.application.participation_progress import membership_progress_text
from tg_lottery.domain.enums import QualificationState
from tg_lottery.presentation.condition_progress import condition_line
from tg_lottery.telegram import member_condition_views
from tg_lottery.telegram.keyboard_layout import columns
from tg_lottery.telegram.member_popups import failure_reply_text
from tg_lottery.telegram.ui_style import row_keyboard

MISSING_TARGETS_PAGE_SIZE = 6
PENDING_APPROVAL_STATUS = "join_request_pending"


@dataclass(frozen=True)
class MissingTargetPage:
    targets: tuple[QualificationTarget, ...]
    missing: tuple[QualificationTarget, ...]
    pending: tuple[QualificationTarget, ...]
    unknown: tuple[QualificationTarget, ...]
    number: int
    count: int


def build_missing_targets_reply(
    campaign: object,
    state: QualificationState,
    targets: tuple[QualificationTarget, ...],
    *,
    observations: tuple[dict[str, object], ...],
    page: int,
    target_url: Callable[[object, QualificationTarget], str],
) -> tuple[str, InlineKeyboardMarkup]:
    selected = missing_target_page(targets, observations, page=page)
    rows = _join_rows(campaign, selected.missing, target_url=target_url)
    rows.extend(_navigation_rows(campaign.public_ref, selected))
    rows.append([_verify_button(campaign.public_ref)])
    summary = membership_progress_text(
        targets, observations, include_targets=False, source="刚刚检查"
    )
    by_chat = {int(item["chat_id"]): item for item in observations}
    lines = [failure_reply_text(campaign, state), summary]
    for target in selected.targets:
        text = condition_line(target, by_chat.get(target.chat_id, {}))
        if target in selected.missing and not member_condition_views.target_joinable(
            campaign, target
        ):
            text += "（入口不可用）"
        lines.append(text)
    lines.extend(_profile_requirements(campaign))
    if selected.count > 1:
        lines.append(f"第 {selected.number}/{selected.count} 页")
    return "\n\n".join(filter(None, lines)), row_keyboard(rows)


def missing_target_page(
    targets: tuple[QualificationTarget, ...],
    observations: tuple[dict[str, object], ...],
    *,
    page: int,
) -> MissingTargetPage:
    by_chat = {int(item["chat_id"]): item for item in observations}
    candidates = tuple({target.chat_id: target for target in targets}.values())
    count = max(1, (len(candidates) + MISSING_TARGETS_PAGE_SIZE - 1) // MISSING_TARGETS_PAGE_SIZE)
    page = min(max(1, page), count)
    start = (page - 1) * MISSING_TARGETS_PAGE_SIZE
    selected = candidates[start : start + MISSING_TARGETS_PAGE_SIZE]
    return MissingTargetPage(
        targets=selected,
        missing=tuple(item for item in selected if _category(item, by_chat) == "missing"),
        pending=tuple(item for item in selected if _category(item, by_chat) == "pending"),
        unknown=tuple(item for item in selected if _category(item, by_chat) == "unknown"),
        number=page,
        count=count,
    )


def _profile_requirements(campaign):
    rules = campaign.rules
    lines = []
    if rules.get("username_required"):
        lines.append("用户名要求：必须设置 Telegram 用户名。")
    if rules.get("allowed_regions"):
        lines.append("地区要求：" + "、".join(rules["allowed_regions"]))
    return lines


def _category(
    target: QualificationTarget, observations: dict[int, dict[str, object]]
) -> str | None:
    observation = observations.get(target.chat_id)
    if observation and observation.get("observed_status") == PENDING_APPROVAL_STATUS:
        return "pending"
    state = member_condition_views.target_state(target, observations)
    if state == "ineligible":
        return "missing"
    return "unknown" if state == "unknown" else None


def _join_rows(
    campaign: object,
    targets: tuple[QualificationTarget, ...],
    *,
    target_url: Callable[[object, QualificationTarget], str],
) -> list[list[InlineKeyboardButton]]:
    return columns(
        [
            InlineKeyboardButton(
                text=member_condition_views.target_button(target), url=target_url(campaign, target)
            )
            for target in targets
            if member_condition_views.target_joinable(campaign, target)
        ]
    )


def _navigation_rows(reference: str, page: MissingTargetPage) -> list[list[InlineKeyboardButton]]:
    buttons = []
    if page.number > 1:
        buttons.append(_page_button("← 上一页", reference, page.number - 1))
    if page.number < page.count:
        buttons.append(_page_button("下一页 →", reference, page.number + 1))
    return [buttons] if buttons else []


def _page_button(text: str, reference: str, page: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"member:missing:{reference}:{page}")


def _verify_button(reference: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="重新检查", callback_data=f"member:verify:{reference}")


async def registration_failure_feedback(queries, campaign, *, user_id, text, keyboard):
    detail = await queries.participant_campaign_detail(user_id, campaign.public_ref)
    registered = detail is not None and detail.registration_completed
    status = (
        "⚠️ 已报名，当前资格未通过；原报名与进度保留。"
        if registered
        else "⏳ 报名待完成；补齐前置条件后点击报名。"
    )
    label = "🔄 重新检查" if registered else "🎟️ 报名"
    rows = [
        [
            button.model_copy(update={"text": label})
            if button.callback_data == f"member:verify:{campaign.public_ref}"
            else button
            for button in row
        ]
        for row in keyboard.inline_keyboard
    ]
    return status + "\n\n" + text, row_keyboard(rows)
