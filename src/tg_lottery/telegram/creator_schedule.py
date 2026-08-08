from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tg_lottery.application.records import utcnow

MIN_DRAW_LEAD = timedelta(minutes=5)
QUICK_DRAW_DELAY = timedelta(hours=1)
EVENING_HOUR = 20


def positive_int(value: str, label: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label}必须是正整数") from exc
    if number < 1:
        raise ValueError(f"{label}必须是正整数")
    return number


def validate_draw_time(draw_at: datetime) -> None:
    if draw_at <= utcnow().astimezone(draw_at.tzinfo) + MIN_DRAW_LEAD:
        raise ValueError("开奖时间至少需要晚于当前时间 5 分钟")


def quick_draw_at(action: str, timezone: str) -> datetime:
    now = utcnow().astimezone(ZoneInfo(timezone))
    if action == "draft_draw_hour":
        return now + QUICK_DRAW_DELAY
    day_offset = 1 if action == "draft_draw_tomorrow" else 0
    target = (now + timedelta(days=day_offset)).replace(
        hour=EVENING_HOUR, minute=0, second=0, microsecond=0
    )
    if target <= now + MIN_DRAW_LEAD:
        target += timedelta(days=1)
    return target
