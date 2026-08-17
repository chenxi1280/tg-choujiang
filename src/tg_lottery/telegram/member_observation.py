from datetime import datetime

from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import QualificationState
from tg_lottery.telegram import member_condition_views
from tg_lottery.telegram.qualification import message_threshold_shortfalls


async def apply_message_thresholds(
    stats: MessageStatsService | None,
    state: QualificationState,
    observations: tuple[dict[str, object], ...],
    *,
    attempt: RegistrationAttempt,
    user_id: int,
    observed_at: datetime,
    thresholds: dict[int, int],
) -> tuple[QualificationState, tuple[dict[str, object], ...]]:
    if not thresholds:
        return state, observations
    if stats is None:
        raise RuntimeError("message stats service is required for thresholds")
    member_ok = {
        int(item["chat_id"])
        for item in observations
        if item["state"] == QualificationState.ELIGIBLE.value
    }
    totals = await stats.totals(user_id, tuple(thresholds))
    shortfalls = message_threshold_shortfalls(
        member_ok=member_ok,
        thresholds=thresholds,
        totals=totals,
        observed_at=observed_at,
    )
    if not shortfalls:
        return state, observations
    tagged = member_condition_views.with_target_kinds(shortfalls, attempt.target_kinds)
    return QualificationState.INELIGIBLE, observations + tagged
