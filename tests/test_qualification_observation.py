from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember

from tg_lottery.domain.enums import QualificationState
from tg_lottery.telegram.qualification import observe_memberships


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


async def test_ineligible_wins_over_earlier_unknown_and_all_chats_are_observed() -> None:
    bot = MembershipBot({-1001: "error", -1002: "left"})

    result = await observe_memberships(
        bot,
        chat_ids=(-1001, -1002),
        user_id=201,
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert result.state is QualificationState.INELIGIBLE
    assert bot.calls == [-1001, -1002]
    assert [item["state"] for item in result.observations] == ["unknown", "ineligible"]


async def test_unknown_is_retained_when_no_chat_is_ineligible() -> None:
    bot = MembershipBot({-1001: "member", -1002: "error", -1003: "administrator"})

    result = await observe_memberships(
        bot,
        chat_ids=(-1001, -1002, -1003),
        user_id=201,
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert result.state is QualificationState.UNKNOWN
    assert bot.calls == [-1001, -1002, -1003]
    assert result.error_code == "TelegramBadRequest"


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
