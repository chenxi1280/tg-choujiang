from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from tg_lottery.application.campaigns import PrizeSpec
from tg_lottery.domain.rules import ActivityRule, CampaignDraft, CampaignRules, InviteRule

LOCAL_TIME_FORMAT = "%Y-%m-%d %H:%M"
DEFAULT_CLAIM_INSTRUCTIONS = "请联系活动管理员领取奖品"
SETTLEMENT_DRAIN_SECONDS = 300

ACTIVITY_PRESETS = {
    "standard": ActivityRule(message_threshold=10, reward_tickets=1, daily_ticket_cap=1),
    "active": ActivityRule(message_threshold=5, reward_tickets=1, daily_ticket_cap=2),
}
INVITE_PRESETS = {
    "standard": InviteRule(reward_tickets=1, retention_hours=24),
    "strict": InviteRule(reward_tickets=1, retention_hours=48),
}


def new_draft(*, chat_id: int, group_title: str, timezone: str) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "group_title": group_title,
        "timezone": timezone,
        "title": "",
        "draw_at": "",
        "claim_instructions": DEFAULT_CLAIM_INSTRUCTIONS,
        "prizes": [],
        "activity_preset": "standard",
        "invite_preset": "standard",
        "required_channel_id": None,
    }


def parse_draw_at(value: str, timezone: str) -> datetime:
    try:
        parsed = datetime.strptime(value.strip(), LOCAL_TIME_FORMAT)
    except ValueError as exc:
        raise ValueError("开奖时间格式应为：2026-08-08 18:30") from exc
    return parsed.replace(tzinfo=ZoneInfo(timezone))


def campaign_input(
    data: dict[str, Any], *, now: datetime
) -> tuple[CampaignDraft, tuple[PrizeSpec, ...]]:
    missing = missing_fields(data)
    if missing:
        raise ValueError("请先完成：" + "、".join(missing))
    draw_at = datetime.fromisoformat(str(data["draw_at"]))
    cutoff_at = draw_at - timedelta(seconds=SETTLEMENT_DRAIN_SECONDS)
    if cutoff_at <= now:
        raise ValueError("开奖时间至少需要晚于当前时间 5 分钟")
    rules = rules_from_data(data)
    draft = CampaignDraft(
        group_id=int(data["chat_id"]),
        name=str(data["title"]),
        preheat_at=now,
        cutoff_at=cutoff_at,
        draw_at=draw_at,
        rules=rules,
    )
    instructions = str(data["claim_instructions"])
    prizes = tuple(
        PrizeSpec(str(item["name"]), int(item["winner_count"]), instructions)
        for item in data["prizes"]
    )
    return draft, prizes


def rules_from_data(data: dict[str, Any]) -> CampaignRules:
    return CampaignRules(
        timezone=str(data["timezone"]),
        activity=ACTIVITY_PRESETS[str(data["activity_preset"])],
        invite=INVITE_PRESETS[str(data["invite_preset"])],
        required_channel_id=data.get("required_channel_id"),
        settlement_drain_seconds=SETTLEMENT_DRAIN_SECONDS,
    )


def missing_fields(data: dict[str, Any]) -> tuple[str, ...]:
    missing: list[str] = []
    if not str(data.get("title", "")).strip():
        missing.append("活动标题")
    if not data.get("draw_at"):
        missing.append("开奖时间")
    if not data.get("prizes"):
        missing.append("至少一个奖品")
    return tuple(missing)


def format_local(value: str, timezone: str) -> str:
    if not value:
        return "等待设置"
    return datetime.fromisoformat(value).astimezone(ZoneInfo(timezone)).strftime(LOCAL_TIME_FORMAT)


def panel_text(data: dict[str, Any]) -> str:
    activity = ACTIVITY_PRESETS[str(data["activity_preset"])]
    invite = INVITE_PRESETS[str(data["invite_preset"])]
    prize_lines = _prize_lines(data.get("prizes", []))
    channel = data.get("required_channel_id") or "不限制"
    missing = missing_fields(data)
    status = "可以发布" if not missing else "还需设置：" + "、".join(missing)
    return (
        "🎰 创建抽奖\n\n"
        f"👥 活动群：{data['group_title']}\n"
        f"📝 活动标题：{data.get('title') or '等待设置'}\n"
        f"🌀 开奖时间：{format_local(str(data.get('draw_at', '')), str(data['timezone']))}\n"
        "⏳ 报名截止：开奖前 5 分钟（系统自动）\n"
        f"🎁 奖品设置：\n{prize_lines}\n"
        f"⚠️ 领奖说明：{data['claim_instructions']}\n"
        f"🔥 活跃票：每天 {activity.message_threshold} 条消息奖励 "
        f"{activity.reward_tickets} 票，上限 {activity.daily_ticket_cap} 票\n"
        f"👫 邀请票：每个有效邀请奖励 {invite.reward_tickets} 票，"
        f"留存 {invite.retention_hours} 小时\n"
        f"📣 关注频道：{channel}\n\n"
        f"当前状态：{status}\n"
        "只需设置标题、开奖时间和奖品；其他规则可直接使用默认值。"
    )


def _prize_lines(prizes: list[dict[str, Any]]) -> str:
    if not prizes:
        return "  等待设置"
    return "\n".join(
        f"  {index}. {item['name']} × {item['winner_count']} 人"
        for index, item in enumerate(prizes, start=1)
    )
