import html
from datetime import datetime
from zoneinfo import ZoneInfo


def featured_content(payload, *, digest):
    heading = f"📅 {payload['date']} 每日精选抽奖" if digest else "🎉 新抽奖上线"
    parts = [f"<b>{heading}</b>"]
    buttons = []
    for item in payload["items"]:
        active = item.get("state", "open") == "open"
        action = "join" if active else "rules"
        url = f"https://t.me/{payload['bot_username']}?start={action}_{item['campaign_ref']}"
        label = "点击参与" if active else "查看规则与结果"
        parts.append(_item_text(item, url=url, label=label))
        buttons.append({"text": f"🎟 {item['name'][:40]}" if digest else label, "url": url})
    parts.append("⏰ 以上时间均为北京时间；参与条件和最新状态以活动页面为准。")
    return "\n\n".join(parts), buttons


def _item_text(item, *, url, label):
    lines = [f"🎁 <b>{html.escape(item['name'])}</b>"]
    if item.get("prizes"):
        lines.append("奖品：" + html.escape(item["prizes"]))
    if item.get("groups"):
        lines.append("来自：" + html.escape(item["groups"]))
    state = item.get("state", "open")
    if state != "open":
        labels = {"cancelled": "已取消", "drawn": "已开奖", "paused": "已暂停"}
        lines.append("📌 " + labels.get(state, "报名已结束"))
    lines.append("⌛ 报名截止：" + _time(item["cutoff_at"]))
    draw = "满人后开奖，最晚 " if item.get("full_draw") else ""
    lines.append("🎲 开奖：" + draw + _time(item["draw_at"]))
    lines.append(f'<a href="{html.escape(url, quote=True)}">{label}</a>')
    return "\n".join(lines)


def _time(value):
    return (
        datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    )
