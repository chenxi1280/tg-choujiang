from datetime import datetime, timedelta
from typing import Any

from tg_lottery.application.records import utcnow
from tg_lottery.presentation.daily_bonus import daily_bonus_dates, daily_bonus_rule_text
from tg_lottery.telegram.creator_behavior_views import rule_text
from tg_lottery.telegram.creator_number_controls import (
    MESSAGE_COUNT_PRESETS,
    number_row,
    preset_row,
)


def daily_activity_editor(data, working, *, title, footer):
    rule = working["rule"]
    double = working["double_count"]
    rows = [
        number_row("加成条数", "activity_cycle", rule["messages_per_cycle"]),
        preset_row("cycle", MESSAGE_COUNT_PRESETS, "条", selected=rule["messages_per_cycle"]),
        [
            ("✅ 双算" if double else "双算", "draft_bonus_double:on"),
            ("不双算" if double else "✅ 不双算", "draft_bonus_double:off"),
        ],
        *footer,
    ]
    text = f"🌱 {title} 抽奖加成票设置\n获得方式：发言\n"
    text += daily_bonus_rule_text(double) + "\n双算选项整场统一，保存后生效。\n"
    text += creation_bonus_dates(data) + "\n"
    gate = data.get("admission") or {}
    saved = gate.get("groups", {}).get(str(working["chat_id"]))
    if gate.get("type") != "message" or saved is None:
        return (
            text + f"本群无发言抽奖门槛，双算不适用；每天满{rule['messages_per_cycle']}条。",
            rows,
        )
    text += "抽奖门槛：" + rule_text(saved, "message")
    threshold = saved["daily_messages"]
    if saved.get("count_mode", "daily") == "daily":
        total = (
            max(threshold, rule["messages_per_cycle"])
            if double
            else threshold + rule["messages_per_cycle"]
        )
        text += f"\n本群每天共需{total}条；其他奖励群也须当天达标。"
    else:
        text += f"\n本期门槛完成后，无需每天重做；当天加成要求{rule['messages_per_cycle']}条。"
    return text, rows


def legacy_activity_description(rule: dict[str, Any], cap: object) -> str:
    reward = rule["reward_tickets"]
    if rule["daily_cap_mode"] == "unlimited":
        counting = f"每天每满 {rule['messages_per_cycle']} 条 +{reward}次；不设每日或累计上限。"
    elif rule["daily_max_cycles"] == 1:
        counting = (
            f"每天发言满 {rule['messages_per_cycle']} 条 +{reward}次；每天只奖励一次，"
            "按实际达标天数累计，不要求连续。"
        )
    else:
        counting = f"每天每满 {rule['messages_per_cycle']} 条 +{reward}次；每日上限：{cap} 轮。"
    return (
        counting + "\n文字、图片、表情均计数，不限制字数。"
        "\n北京时间每天 00:00 更新当天统计，之前获得的加成保留。"
        "\n选择预设或点击“自定义”，保存后生效。"
    )


def daily_creation_summary(data, *, activity, invite):
    names = {str(g["id"]): g["title"] for g in data.get("participation_groups", [])}
    conditions = "；".join(
        f"{names.get(k, '活动群')}每天{r['messages_per_cycle']}条"
        for k, r in data.get("group_activity_rules", {}).items()
        if r.get("enabled")
    )
    summary = (
        daily_bonus_rule_text(data.get("activity_bonus_double_count", True))
        if activity
        else "发言加成票关闭"
    )
    return f"{summary}\n{conditions}\n{creation_bonus_dates(data)}\n邀请加成票：{len(invite)}个群"


def creation_bonus_dates(data):
    if not data.get("draw_at"):
        return "设置开始和开奖时间后显示具体可获票日期。"
    start = datetime.fromisoformat(data["start_at"]) if data.get("start_at") else utcnow()
    cutoff = datetime.fromisoformat(data["draw_at"]) - timedelta(minutes=5)
    return daily_bonus_dates(start, cutoff)
