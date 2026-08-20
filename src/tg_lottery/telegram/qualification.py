from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)

from tg_lottery.domain.enums import QualificationState

_USER_ABSENT_MESSAGES = ("user not found", "user_not_participant", "not participant")
_TARGET_UNAVAILABLE_MESSAGES = (
    "chat not found",
    "bot is not a member",
    "not enough rights",
    "have no rights",
    "need administrator rights",
)
_TARGET_EXCEPTIONS = (
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramUnauthorizedError,
)
_RETRYABLE_EXCEPTIONS = (TelegramNetworkError, TelegramServerError)


class QualificationRetryable(Exception):
    pass


class QualificationRateLimited(QualificationRetryable):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Telegram qualification rate limited for {retry_after}s")
        self.retry_after = retry_after


@dataclass(frozen=True)
class TargetPreflightFailure:
    chat_id: int
    error_code: str
    details: str


class SmoothRateLimiter:
    def __init__(
        self,
        requests_per_second: float,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("qualification rate must be positive")
        self._interval = 1 / requests_per_second
        self._sleep = sleep
        self._clock = clock
        self._next_at: float | None = None

    async def acquire(self) -> None:
        now = self._clock()
        if self._next_at is not None and now < self._next_at:
            await self._sleep(self._next_at - now)
            now = self._clock()
        self._next_at = max(now, self._next_at or now) + self._interval


@dataclass(frozen=True)
class QualificationObservationResult:
    state: QualificationState
    observed_status: str
    error_code: str | None
    observations: tuple[dict[str, Any], ...]


async def preflight_membership_targets(
    bot: Bot,
    *,
    chat_ids: tuple[int, ...],
    limiter: SmoothRateLimiter,
) -> tuple[TargetPreflightFailure, ...]:
    if not chat_ids:
        return ()
    bot_id = int(bot.id)
    failures = []
    for chat_id in chat_ids:
        failure = await _preflight_target(bot, chat_id=chat_id, bot_id=bot_id, limiter=limiter)
        if failure is not None:
            failures.append(failure)
    return tuple(failures)


async def observe_memberships(
    bot: Bot,
    *,
    chat_ids: tuple[int, ...],
    user_id: int,
    observed_at: datetime,
    limiter: SmoothRateLimiter | None = None,
) -> QualificationObservationResult:
    rate_limiter = limiter or SmoothRateLimiter(20)
    observations = []
    for chat_id in chat_ids:
        observations.append(
            await _observe_membership(
                bot,
                chat_id=chat_id,
                user_id=user_id,
                observed_at=observed_at,
                limiter=rate_limiter,
            )
        )
    values = tuple(observations)
    state = _aggregate_state(values)
    errors = sorted({str(item["error_code"]) for item in values if item.get("error_code")})
    return QualificationObservationResult(
        state=state,
        observed_status=f"{len(values)}_chats:{state.value}" if values else "not_required",
        error_code=",".join(errors) or None,
        observations=values,
    )


async def _preflight_target(
    bot: Bot, *, chat_id: int, bot_id: int, limiter: SmoothRateLimiter
) -> TargetPreflightFailure | None:
    try:
        await limiter.acquire()
        member = await bot.get_chat_member(chat_id, bot_id)
    except TelegramRetryAfter as exc:
        raise QualificationRateLimited(exc.retry_after) from exc
    except _TARGET_EXCEPTIONS as exc:
        return _target_failure(chat_id, exc)
    except TelegramBadRequest as exc:
        return _target_failure(chat_id, exc)
    except _RETRYABLE_EXCEPTIONS as exc:
        raise QualificationRetryable(str(exc)) from exc
    except TelegramAPIError as exc:
        raise QualificationRetryable(str(exc)) from exc
    if not _is_member(member):
        return TargetPreflightFailure(chat_id, "bot_not_member", str(member.status))
    return None


async def _observe_membership(
    bot: Bot,
    *,
    chat_id: int,
    user_id: int,
    observed_at: datetime,
    limiter: SmoothRateLimiter,
) -> dict[str, Any]:
    try:
        await limiter.acquire()
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramRetryAfter as exc:
        raise QualificationRateLimited(exc.retry_after) from exc
    except TelegramBadRequest as exc:
        if _contains(str(exc), _USER_ABSENT_MESSAGES):
            return _observation(
                chat_id=chat_id,
                state=QualificationState.INELIGIBLE,
                status="not_participant",
                error=exc,
                observed_at=observed_at,
            )
        if _contains(str(exc), _TARGET_UNAVAILABLE_MESSAGES):
            raise QualificationRetryable(f"target {chat_id} unavailable: {exc}") from exc
        raise QualificationRetryable(str(exc)) from exc
    except _TARGET_EXCEPTIONS as exc:
        raise QualificationRetryable(f"target {chat_id} unavailable: {exc}") from exc
    except _RETRYABLE_EXCEPTIONS as exc:
        raise QualificationRetryable(str(exc)) from exc
    except TelegramAPIError as exc:
        raise QualificationRetryable(str(exc)) from exc
    state = QualificationState.ELIGIBLE if _is_member(member) else QualificationState.INELIGIBLE
    return _observation(
        chat_id=chat_id,
        state=state,
        status=str(member.status),
        error=None,
        observed_at=observed_at,
    )


def _observation(
    *,
    chat_id: int,
    state: QualificationState,
    status: str,
    error: Exception | None,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "state": state.value,
        "observed_status": status,
        "error_code": type(error).__name__ if error else None,
        "observed_at": observed_at.isoformat(),
    }


def _target_failure(chat_id: int, exc: Exception) -> TargetPreflightFailure:
    return TargetPreflightFailure(chat_id, type(exc).__name__, str(exc))


def _contains(message: str, values: tuple[str, ...]) -> bool:
    normalized = message.casefold().replace(" ", "_")
    return any(value.replace(" ", "_") in normalized for value in values)


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


def message_threshold_shortfalls(
    *,
    member_ok: set[int],
    thresholds: dict[int, int],
    totals: dict[int, int],
    observed_at: datetime,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "chat_id": chat_id,
            "state": "ineligible",
            "observed_status": f"messages {totals.get(chat_id, 0)}/{need}",
            "error_code": "message_threshold",
            "observed_at": observed_at.isoformat(),
        }
        for chat_id, need in thresholds.items()
        if chat_id in member_ok and totals.get(chat_id, 0) < need
    )


def with_pending_approvals(
    observations: tuple[dict[str, Any], ...], pending_chat_ids: frozenset[int]
) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(item) | {"observed_status": "join_request_pending"}
        if int(item["chat_id"]) in pending_chat_ids and item.get("state") == "ineligible"
        else item
        for item in observations
    )
