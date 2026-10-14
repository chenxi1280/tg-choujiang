from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.campaign_targets import QualificationTarget
from tg_lottery.application.participation_progress import COUNTING_NOTICE, membership_progress_text
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import member_condition_views
from tg_lottery.telegram.ui_style import row_keyboard

MISSING_TARGETS_PAGE_SIZE = 6
PENDING_APPROVAL_STATUS = "join_request_pending"


@dataclass(frozen=True)
class MissingTargetPage:
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
    text = member_condition_views.missing_targets_text(
        campaign,
        state,
        missing=list(selected.missing),
        pending=list(selected.pending),
        unknown=list(selected.unknown),
        page=(selected.number, selected.count),
    )
    summary = membership_progress_text(targets, observations, include_targets=False)
    return "\n\n".join(filter(None, [text, summary, COUNTING_NOTICE])), row_keyboard(rows)


def missing_target_page(
    targets: tuple[QualificationTarget, ...],
    observations: tuple[dict[str, object], ...],
    *,
    page: int,
) -> MissingTargetPage:
    by_chat = {int(item["chat_id"]): item for item in observations}
    candidates = tuple(target for target in targets if _category(target, by_chat))
    count = max(1, (len(candidates) + MISSING_TARGETS_PAGE_SIZE - 1) // MISSING_TARGETS_PAGE_SIZE)
    if page < 1 or page > count:
        raise UserFacingError("缺失条件页码已经失效，请重新验证资格")
    start = (page - 1) * MISSING_TARGETS_PAGE_SIZE
    selected = candidates[start : start + MISSING_TARGETS_PAGE_SIZE]
    return MissingTargetPage(
        missing=tuple(item for item in selected if _category(item, by_chat) == "missing"),
        pending=tuple(item for item in selected if _category(item, by_chat) == "pending"),
        unknown=tuple(item for item in selected if _category(item, by_chat) == "unknown"),
        number=page,
        count=count,
    )


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
    return [
        [
            InlineKeyboardButton(
                text=member_condition_views.target_button(target),
                url=target_url(campaign, target),
            )
        ]
        for target in targets
        if member_condition_views.target_joinable(campaign, target)
    ]


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
    return InlineKeyboardButton(text="验证资格", callback_data=f"member:verify:{reference}")
