from zoneinfo import ZoneInfo

from tg_lottery.domain.message_calendar import task_dates


def calendar_group_lines(campaign, rule, progress, *, name, day, counts):
    today_done = day in task_dates(campaign) and progress.today_messages >= rule.daily_messages
    today_status = "今日任务已完成" if today_done else "今日任务待完成"
    lines = [
        f"{name}: 今日 {progress.today_messages}/{rule.daily_messages} 条 · {today_status}",
        f"本期每日任务: 已完成 {progress.consecutive_days}/{rule.consecutive_days} 天",
    ]
    for date in task_dates(campaign):
        status = "待开始" if date > day else "未完成"
        if date <= day and counts.get(date, 0) >= rule.daily_messages:
            status = "已完成"
        lines.append(f"{date}: {status}")
    return lines


def calendar_window_text(campaign):
    timezone = ZoneInfo(campaign.timezone)
    start = campaign.preheat_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
    cutoff = campaign.cutoff_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
    return f"本期任务时间: {start} 至 {cutoff} (北京时间); 各任务日须分别达标, 多发不抵次日。"
