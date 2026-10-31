"""Shared wording for the daily, campaign-wide speech bonus."""

from datetime import timedelta
from zoneinfo import ZoneInfo


def daily_bonus_rule_text(double_count):
    counting = (
        "双算：抽奖门槛发言同时计入加成"
        if double_count
        else "不双算：完成抽奖门槛后的下一条发言起计加成"
    )
    return (
        "抽奖加成票（发言获得）：首日不发，第二天起整场每天最多1张；"
        "所有奖励群当天条件达标后自动发放。\n" + counting
    )


def daily_bonus_progress_text(state, *, names):
    if state.phase == "closed":
        return f"🌱 抽奖加成票（发言获得）\n已停止新增，历史累计{state.total_awarded}张。"
    lines = [
        "🌱 抽奖加成票（发言获得）",
        _daily_status(state),
        f"累计{state.total_awarded}张；能否生效仍需核验整场参与条件。",
    ]
    for group in state.groups:
        gate = "本群发票前置条件已满足" if group.gate_ready else "相关发言门槛尚未完成，暂不发票"
        lines.append(
            f"{names.get(str(group.chat_id), '活动群')}：今日加成发言 "
            f"{group.messages}/{group.required} 条；{gate}"
        )
    return "\n".join(lines)


def _daily_status(state):
    if state.phase == "paused":
        return "活动暂停，停止新增计数，已有加成票保留。"
    if state.phase == "scheduled":
        return f"活动尚未运行，首次可获加成票日期：{state.starts_on}。"
    if state.first_day:
        return f"首日不发加成票，首次可获票日期：{state.starts_on}。"
    if state.awarded:
        return "今日已获1/1张，继续发言不再增加今日加成票。"
    return "今日已获0/1张，所有奖励群条件达标后自动发放。"


def daily_bonus_dates(start_at, cutoff_at):
    timezone = ZoneInfo("Asia/Shanghai")
    first = start_at.astimezone(timezone).date()
    last = cutoff_at.astimezone(timezone).date()
    days = max(0, (last - first).days)
    if not days:
        return f"活动仅覆盖{first}，本场可获得0张发言来源的抽奖加成票。"
    return (
        f"首日{first}不发；可获票日期：{first + timedelta(days=1)}至{last}；"
        f"本场最多{days}张发言来源的抽奖加成票。"
    )
