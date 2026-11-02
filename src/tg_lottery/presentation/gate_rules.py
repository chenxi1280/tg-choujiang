"""Descriptions of the frozen speech gate modes, shared across public views."""

from datetime import datetime

from tg_lottery.domain.message_calendar import task_date_range
from tg_lottery.telegram.public_projection import format_public_time


def payload_task_dates(payload, rules):
    if not rules.message_task_calendar:
        return ()
    return task_date_range(
        datetime.fromisoformat(str(payload["preheat_at"])),
        datetime.fromisoformat(str(payload["cutoff_at"])),
        timezone=rules.timezone,
    )


def task_date_text(dates):
    def label(day):
        return format_public_time(day, "Asia/Shanghai", include_timezone=False, date_only=True)

    if len(dates) > 4:
        return f"{label(dates[0])}至{label(dates[-1])}共 {len(dates)} 天"
    return "、".join(label(day) for day in dates)


def gate_rule_text(rule, *, calendar_dates=()):
    if calendar_dates:
        return f"{task_date_text(calendar_dates)}每天各满 {rule.daily_messages} 条"
    if rule.count_mode == "period":
        return f"本期累计有效发言满 {rule.daily_messages} 条"
    period = "任意 1 天" if rule.consecutive_days == 1 else f"连续 {rule.consecutive_days} 天，每天"
    return f"{period}有效发言满 {rule.daily_messages} 条"


def gate_mode_notes(groups, *, names=None, fixed_dates=False):
    if not groups:
        return ()
    if fixed_dates:
        return (
            "固定任务日：按北京时间自然日分别统计，每个指定日期分别达标，多发不能抵其他日期，未来日期不能提前完成。",
        )
    notes = []
    modes = {rule.count_mode for rule in groups.values()}
    if "period" in modes:
        notes.append(
            "本期累计：报名通过后至截止前累计达标即可，各群分别计算；跨日累加，不按天清零。"
        )
    if "daily" in modes:
        prefix = _daily_prefix(groups, names or {}, mixed=len(modes) > 1)
        notes.append(
            prefix + "按北京时间自然日计（0 点换日），各群分别达标；"
            "完成完整周期后，本群门槛无需重做，参与开奖仍须满足全部参与条件。"
        )
        if any(
            rule.count_mode == "daily" and rule.consecutive_days > 1 for rule in groups.values()
        ):
            notes.append("未完成完整周期时，中断后连续天数重新计算。")
    return tuple(notes)


def _daily_prefix(groups, names, *, mixed):
    if mixed:
        titles = "、".join(
            names.get(key, key) for key, rule in groups.items() if rule.count_mode == "daily"
        )
        return f"{titles}每日门槛："
    return "每日门槛："
