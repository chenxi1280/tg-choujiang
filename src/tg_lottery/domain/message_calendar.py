"""Fixed campaign task dates, independent of personal registration dates."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from tg_lottery.domain.base_activity import BaseProgress


def task_dates(campaign):
    timezone = ZoneInfo(campaign.timezone)
    first = campaign.preheat_at.astimezone(timezone).date()
    last = campaign.cutoff_at.astimezone(timezone).date()
    return tuple(first + timedelta(days=n) for n in range((last - first).days + 1))


def calendar_progress(counts, rule, *, today, dates):
    completed = sum(day <= today and counts.get(day, 0) >= rule.daily_messages for day in dates)
    qualified = bool(dates) and completed == len(dates) == rule.consecutive_days
    return BaseProgress(counts.get(today, 0), completed, qualified)
