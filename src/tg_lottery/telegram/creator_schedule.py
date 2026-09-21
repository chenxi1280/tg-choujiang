from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.public_projection import BEIJING_TIMEZONE

MIN_DRAW_LEAD = timedelta(minutes=5)
QUICK_DRAW_DAYS = {"draft_draw_week": 7, "draft_draw_ten_days": 10}
EVENING_HOUR = 20


def positive_int(value: str, label: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as exc:
        raise UserFacingError(f"{label}必须是正整数") from exc
    if number < 1:
        raise UserFacingError(f"{label}必须是正整数")
    return number


def validate_draw_time(draw_at: datetime) -> None:
    if draw_at <= utcnow().astimezone(draw_at.tzinfo) + MIN_DRAW_LEAD:
        raise UserFacingError("开奖时间至少需要晚于当前时间 5 分钟")


def quick_draw_at(action: str, timezone: str, *, now: datetime | None = None) -> datetime:
    now = (now or utcnow()).astimezone(ZoneInfo(BEIJING_TIMEZONE))
    if action in QUICK_DRAW_DAYS:
        return now + timedelta(days=QUICK_DRAW_DAYS[action])
    if action != "draft_draw_tomorrow":
        raise UserFacingError("快捷时间已更新，请重新打开定时开奖页面")
    day_offset = 1
    target = (now + timedelta(days=day_offset)).replace(
        hour=EVENING_HOUR, minute=0, second=0, microsecond=0
    )
    if target <= now + MIN_DRAW_LEAD:
        target += timedelta(days=1)
    return target
