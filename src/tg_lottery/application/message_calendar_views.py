from tg_lottery.domain.message_calendar import task_dates
from tg_lottery.telegram.public_projection import format_public_time


def calendar_group_lines(campaign, rule, progress, *, name, day, counts, now):
    today_done = day in task_dates(campaign) and progress.today_messages >= rule.daily_messages
    today_status = "✅ 今日任务已完成" if today_done else "❌ 今日任务待完成"
    if now < campaign.preheat_at:
        today_status = "⏳ 待开始"
    lines = [
        f"{name}\uff1a 今日 {progress.today_messages}/{rule.daily_messages} 条 · {today_status}",
        f"本期每日任务\uff1a 已完成 {progress.consecutive_days}/{rule.consecutive_days} 天",
    ]
    for date in task_dates(campaign):
        status = "⏳ 待开始" if date > day or now < campaign.preheat_at else "❌ 未完成"
        if date <= day and counts.get(date, 0) >= rule.daily_messages:
            status = "✅ 已完成"
        label = format_public_time(date, campaign.timezone, include_timezone=False, date_only=True)
        lines.append(f"{label}\uff1a{status}")
    return lines


def calendar_window_text(campaign, *, include_timezone=True):
    start = format_public_time(campaign.preheat_at, campaign.timezone, include_timezone=False)
    cutoff = format_public_time(campaign.cutoff_at, campaign.timezone, include_timezone=False)
    label = "\uff08北京时间\uff09" if include_timezone else ""
    return (
        f"本期任务时间\uff1a {start} 至 {cutoff}{label}\uff1b "
        + "各任务日须分别达标\uff0c 多发不抵次日。"
    )
