from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tg_lottery.domain.enums import QualificationState


@dataclass(frozen=True)
class QualificationObservationResult:
    state: QualificationState
    observed_status: str
    error_code: str | None
    observations: tuple[dict[str, Any], ...]


async def observe_memberships(
    bot: Bot,
    *,
    chat_ids: tuple[int, ...],
    user_id: int,
    observed_at: datetime,
) -> QualificationObservationResult:
    observations = tuple(
        [
            await _observe_membership(
                bot, chat_id=chat_id, user_id=user_id, observed_at=observed_at
            )
            for chat_id in chat_ids
        ]
    )
    state = _aggregate_state(observations)
    errors = sorted({str(item["error_code"]) for item in observations if item.get("error_code")})
    return QualificationObservationResult(
        state=state,
        observed_status=f"{len(observations)}_chats:{state.value}"
        if observations
        else "not_required",
        error_code=",".join(errors) or None,
        observations=observations,
    )


async def _observe_membership(
    bot: Bot, *, chat_id: int, user_id: int, observed_at: datetime
) -> dict[str, Any]:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError as exc:
        return _observation(
            chat_id=chat_id,
            state=QualificationState.UNKNOWN,
            status="unavailable",
            error_code=type(exc).__name__,
            observed_at=observed_at,
        )
    status = str(member.status)
    state = QualificationState.ELIGIBLE if _is_member(member) else QualificationState.INELIGIBLE
    return _observation(
        chat_id=chat_id,
        state=state,
        status=status,
        error_code=None,
        observed_at=observed_at,
    )


def _observation(
    *,
    chat_id: int,
    state: QualificationState,
    status: str,
    error_code: str | None,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "state": state.value,
        "observed_status": status,
        "error_code": error_code,
        "observed_at": observed_at.isoformat(),
    }


def _aggregate_state(observations: tuple[dict[str, Any], ...]) -> QualificationState:
    states = {QualificationState(str(item["state"])) for item in observations}
    if QualificationState.INELIGIBLE in states:
        return QualificationState.INELIGIBLE
    if QualificationState.UNKNOWN in states:
        return QualificationState.UNKNOWN
    return QualificationState.ELIGIBLE


def _is_member(member: object) -> bool:
    status = str(getattr(member, "status", ""))
    if status in {"member", "administrator", "creator"}:
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))
