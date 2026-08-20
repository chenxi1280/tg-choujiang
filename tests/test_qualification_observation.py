from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember

from tg_lottery.domain.enums import QualificationState
from tg_lottery.telegram.qualification import QualificationRetryable, observe_memberships


class MembershipBot:
    def __init__(self, outcomes: dict[int, str]) -> None:
        self.outcomes = outcomes
        self.calls: list[int] = []

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.calls.append(chat_id)
        status = self.outcomes[chat_id]
        if status == "error":
            raise TelegramBadRequest(
                method=GetChatMember(chat_id=chat_id, user_id=user_id),
                message="temporary failure",
            )
        return SimpleNamespace(status=status, is_member=status == "restricted")


async def test_transient_failure_stops_review_for_explicit_retry() -> None:
    bot = MembershipBot({-1001: "error", -1002: "left"})

    with pytest.raises(QualificationRetryable):
        await observe_memberships(
            bot,
            chat_ids=(-1001, -1002),
            user_id=201,
            observed_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
    assert bot.calls == [-1001]


async def test_transient_failure_is_not_persisted_as_unknown() -> None:
    bot = MembershipBot({-1001: "member", -1002: "error", -1003: "administrator"})

    with pytest.raises(QualificationRetryable):
        await observe_memberships(
            bot,
            chat_ids=(-1001, -1002, -1003),
            user_id=201,
            observed_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
    assert bot.calls == [-1001, -1002]


async def test_empty_requirement_set_is_eligible() -> None:
    result = await observe_memberships(
        MembershipBot({}),
        chat_ids=(),
        user_id=201,
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert result.state is QualificationState.ELIGIBLE
    assert result.observed_status == "not_required"
    assert result.observations == ()
