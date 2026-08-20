from datetime import UTC, datetime

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import GetChatMember

from tg_lottery.domain.enums import QualificationState
from tg_lottery.telegram.qualification import (
    QualificationRateLimited,
    SmoothRateLimiter,
    observe_memberships,
    preflight_membership_targets,
)


class ErrorBot:
    id = 900

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        self._message = message
        self._retry_after = retry_after

    async def get_chat_member(self, chat_id: int, user_id: int):
        method = GetChatMember(chat_id=chat_id, user_id=user_id)
        if self._retry_after is not None:
            raise TelegramRetryAfter(method, self._message, self._retry_after)
        raise TelegramBadRequest(method, self._message)


async def test_user_not_found_is_ineligible_instead_of_unknown() -> None:
    result = await observe_memberships(
        ErrorBot("Bad Request: user not found"),
        chat_ids=(-1001,),
        user_id=201,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )

    assert result.state is QualificationState.INELIGIBLE
    assert result.observations[0]["observed_status"] == "not_participant"


async def test_chat_not_found_is_detected_by_target_preflight() -> None:
    failures = await preflight_membership_targets(
        ErrorBot("Bad Request: chat not found"),
        chat_ids=(-1001,),
        limiter=SmoothRateLimiter(1_000_000),
    )

    assert len(failures) == 1
    assert failures[0].chat_id == -1001
    assert failures[0].error_code == "TelegramBadRequest"


async def test_retry_after_preserves_telegram_delay() -> None:
    with pytest.raises(QualificationRateLimited) as raised:
        await observe_memberships(
            ErrorBot("Too Many Requests", retry_after=7),
            chat_ids=(-1001,),
            user_id=201,
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        )

    assert raised.value.retry_after == 7


async def test_smooth_limiter_spaces_requests_without_burst() -> None:
    clock_value = [0.0]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock_value[0] += delay

    limiter = SmoothRateLimiter(2, sleep=sleep, clock=lambda: clock_value[0])
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()

    assert sleeps == [0.5, 0.5]
