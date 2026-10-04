from typing import Any

from tg_lottery.domain.rules import InviteRule

HOURS_BY_ACTION = {
    "draft_retention_now": 0,
    "draft_retention_day": 24,
    "draft_retention_two_days": 48,
}
ACTIONS = frozenset({"draft_retention", *HOURS_BY_ACTION})


def retention_hours(data: dict[str, Any]) -> int:
    if data.get("invite_retention_hours") is not None:
        return InviteRule(retention_hours=data["invite_retention_hours"]).retention_hours
    return HOURS_BY_ACTION[
        "draft_retention_two_days"
        if data.get("invite_preset") == "strict"
        else "draft_retention_day"
    ]


def retention_text(hours: int) -> str:
    return f"入群满 {hours} 小时后核验" if hours else "入群后立即核验（无需留存等待）"


def summary(data: dict[str, Any]) -> str:
    gate = data.get("admission") or {}
    if "invite" not in data.get("bonus_types", []) and gate.get("type") != "invite":
        return ""
    return "邀请资格：" + retention_text(retention_hours(data)) + "\n"


def apply_action(data: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "draft_retention":
        return dict(data)
    return data | {"invite_retention_hours": HOURS_BY_ACTION[action]}


def view(data: dict[str, Any]) -> tuple[str, list[list[tuple[str, str]]]]:
    text = (
        "👥 本场邀请留存要求\n\n当前："
        + retention_text(retention_hours(data))
        + "\n\n达到留存时间后仍需核验新成员资格。已有成员、重复入群和自邀不计奖。"
        "\n最晚计奖入群时间 = 报名截止时间减去留存时间。"
        "\n留存设置不会改变邀请链接在开奖后仍可入群的规则。"
    )
    rows = [
        [("入群后立即核验", "draft_retention_now")],
        [("留存 24 小时", "draft_retention_day"), ("留存 48 小时", "draft_retention_two_days")],
        [("↩ 返回加成设置", "draft_bonus_menu")],
    ]
    return text, rows
