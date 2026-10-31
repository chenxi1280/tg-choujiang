from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from tg_lottery.domain.rules import BaseActivityRule


@dataclass(frozen=True)
class BaseProgress:
    today_messages: int
    consecutive_days: int
    qualified: bool


def base_progress(counts: Mapping[date, int], rule: BaseActivityRule, today: date) -> BaseProgress:
    if rule.count_mode == "period":
        total = sum(count for day, count in counts.items() if day <= today)
        qualified = total >= rule.daily_messages
        return BaseProgress(total, int(qualified), qualified)
    completed = sorted(day for day, count in counts.items() if count >= rule.daily_messages)
    streak = best = 0
    previous = None
    for day in completed:
        streak = streak + 1 if previous == day - timedelta(days=1) else 1
        best = max(best, streak)
        previous = day
    current = streak if previous in (today, today - timedelta(days=1)) else 0
    return BaseProgress(counts.get(today, 0), current, best >= rule.consecutive_days)
