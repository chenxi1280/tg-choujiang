from datetime import datetime, timedelta

from tg_lottery.application.records import utcnow
from tg_lottery.telegram.creator_invite_retention import retention_hours


def bonus_window_warning(data, *, now=None):
    gate = data.get("admission") or {}
    if "invite" not in data.get("bonus_types", []) or gate.get("type") == "invite":
        return ""
    if not data.get("draw_at"):
        return ""
    cutoff = datetime.fromisoformat(data["draw_at"]) - timedelta(seconds=300)
    if (now or utcnow()) <= cutoff - timedelta(hours=retention_hours(data)):
        return ""
    return "\n提示：本场剩余时间不足以完成邀请留存，邀请加成票无可完成窗口；发言门槛仍可完成。\n"
