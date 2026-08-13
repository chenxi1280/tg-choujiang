from __future__ import annotations

from datetime import datetime, time, timedelta
from secrets import token_urlsafe
from typing import Any
from zoneinfo import ZoneInfo

from tg_lottery.application.campaigns import PrizeSpec
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import (
    ActivityRule,
    CampaignDraft,
    CampaignRules,
    InviteRewardTier,
    InviteRule,
)

LOCAL_TIME_FORMAT = "%Y-%m-%d %H:%M"
DEFAULT_CLAIM_INSTRUCTIONS = "请联系活动管理员领取奖品"
SETTLEMENT_DRAIN_SECONDS = 300
FULL_DRAW_FALLBACK_DAYS = 30
DEFAULT_INVITE_REWARD_TIERS = (
    {"invite_count": 1, "total_bonus": 1},
    {"invite_count": 2, "total_bonus": 3},
)
DEFAULT_INVITE_BONUS_CAP = 3
DEFAULT_ACTIVITY_MESSAGE_THRESHOLD = 10
DEFAULT_ACTIVITY_REWARD_TICKETS = 1

INVITE_PRESETS = {
    "standard": InviteRule(reward_tickets=1, retention_hours=24),
    "strict": InviteRule(reward_tickets=1, retention_hours=48),
}


def new_draft(
    *,
    chat_id: int | None = None,
    group_title: str = "",
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    return {
        "draft_id": token_urlsafe(18),
        "draft_revision": 0,
        "chat_id": chat_id,
        "group_title": group_title,
        "timezone": timezone,
        "title": "",
        "draw_at": "",
        "draw_mode": "fixed",
        "participant_target": None,
        "claim_instructions": DEFAULT_CLAIM_INSTRUCTIONS,
        "prizes": [],
        "invite_preset": "standard",
        "required_channel_id": None,
        "required_chat_ids": [],
        "required_chat_refs": [],
        "required_chats": [],
        "cover_file_id": None,
        "description": "",
        "username_required": False,
        "allowed_regions": [],
        "issuer": "",
        "issuer_name": "",
        "luck_mode": "off",
        "bonus_types": [],
        "invite_reward_tiers": [dict(item) for item in DEFAULT_INVITE_REWARD_TIERS],
        "invite_bonus_cap": DEFAULT_INVITE_BONUS_CAP,
        "activity_message_threshold": DEFAULT_ACTIVITY_MESSAGE_THRESHOLD,
        "activity_reward_tickets": DEFAULT_ACTIVITY_REWARD_TICKETS,
        "message_tips": True,
        "delete_trigger_message": False,
        "completion_delete_after_seconds": None,
    }


def ensure_draft_identity(data: dict[str, Any]) -> dict[str, Any]:
    defaults = new_draft()
    merged = defaults | data
    if data.get("draft_id"):
        return merged
    return merged | {"draft_id": token_urlsafe(18), "draft_revision": 0}


def parse_draw_at(value: str, timezone: str) -> datetime:
    try:
        parsed = datetime.strptime(value.strip(), LOCAL_TIME_FORMAT)
    except ValueError as exc:
        raise UserFacingError("开奖时间格式应为：2026-08-08 18:30") from exc
    return parsed.replace(tzinfo=ZoneInfo(timezone))


def campaign_input(
    data: dict[str, Any], *, now: datetime
) -> tuple[CampaignDraft, tuple[PrizeSpec, ...]]:
    missing = missing_fields(data)
    if missing:
        raise UserFacingError("请先完成：" + "、".join(missing))
    draw_at = _draw_at(data, now)
    cutoff_at = draw_at - timedelta(seconds=SETTLEMENT_DRAIN_SECONDS)
    if cutoff_at <= now:
        raise UserFacingError("开奖时间至少需要晚于当前时间 5 分钟")
    rules = rules_from_data(data)
    draft = CampaignDraft(
        group_id=int(data["chat_id"]),
        name=str(data["title"]),
        preheat_at=now,
        cutoff_at=cutoff_at,
        draw_at=draw_at,
        rules=rules,
        presentation=presentation_from_data(data),
    )
    instructions = str(data["claim_instructions"])
    prizes = tuple(
        PrizeSpec(str(item["name"]), int(item["winner_count"]), instructions)
        for item in data["prizes"]
    )
    return draft, prizes


def rules_from_data(data: dict[str, Any]) -> CampaignRules:
    activity_reward = int(data.get("activity_reward_tickets", DEFAULT_ACTIVITY_REWARD_TICKETS))
    activity = ActivityRule(
        message_threshold=int(
            data.get("activity_message_threshold", DEFAULT_ACTIVITY_MESSAGE_THRESHOLD)
        ),
        reward_tickets=activity_reward,
        daily_ticket_cap=activity_reward,
        settle_once_per_day=True,
    )
    raw_tiers = data.get("invite_reward_tiers", DEFAULT_INVITE_REWARD_TIERS)
    tiers = tuple(InviteRewardTier.model_validate(item) for item in raw_tiers)
    invite = InviteRule(
        retention_hours=INVITE_PRESETS[str(data["invite_preset"])].retention_hours,
        reward_tiers=tiers,
        max_bonus_tickets=int(data.get("invite_bonus_cap", DEFAULT_INVITE_BONUS_CAP)),
    )
    return CampaignRules(
        timezone=str(data["timezone"]),
        activity=activity,
        invite=invite,
        required_channel_id=data.get("required_channel_id"),
        required_chat_ids=tuple(int(value) for value in data.get("required_chat_ids", [])),
        username_required=bool(data.get("username_required")),
        allowed_regions=tuple(str(value) for value in data.get("allowed_regions", [])),
        draw_mode=str(data.get("draw_mode", "fixed")),
        participant_target=data.get("participant_target"),
        luck_mode=str(data.get("luck_mode", "off")),
        bonus_types=_bonus_types(data),
        settlement_drain_seconds=SETTLEMENT_DRAIN_SECONDS,
    )


def missing_fields(data: dict[str, Any]) -> tuple[str, ...]:
    missing: list[str] = []
    if data.get("chat_id") is None:
        missing.append("活动群")
    if not str(data.get("title", "")).strip():
        missing.append("抽奖标题")
    if not data.get("prizes"):
        missing.append("至少一个奖品")
    if data.get("draw_mode") == "full" and not data.get("participant_target"):
        missing.append("满人数量")
    if data.get("draw_mode") != "full" and not data.get("draw_at"):
        missing.append("开奖时间")
    return tuple(missing)


def presentation_from_data(data: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "cover_file_id",
        "description",
        "username_required",
        "allowed_regions",
        "required_chat_ids",
        "required_chats",
        "main_chat",
        "issuer",
        "issuer_name",
        "issuer_user_id",
        "issuer_username_at_publish",
        "issuer_display_name_at_publish",
        "issuer_source",
        "luck_mode",
        "bonus_types",
        "invite_reward_tiers",
        "invite_bonus_cap",
        "activity_message_threshold",
        "activity_reward_tickets",
        "message_tips",
        "delete_trigger_message",
        "completion_delete_after_seconds",
        "draw_mode",
        "participant_target",
    )
    return {key: data.get(key) for key in keys}


def _draw_at(data: dict[str, Any], now: datetime) -> datetime:
    if data.get("draw_mode") == "full":
        timezone = ZoneInfo(str(data["timezone"]))
        fallback_date = now.astimezone(timezone).date() + timedelta(days=FULL_DRAW_FALLBACK_DAYS)
        return datetime.combine(fallback_date, time(hour=12), tzinfo=timezone)
    return datetime.fromisoformat(str(data["draw_at"]))


def _bonus_types(data: dict[str, Any]) -> tuple[str, ...]:
    if "bonus_types" in data:
        return tuple(data["bonus_types"])
    return ("invite", "activity") if data.get("luck_mode") != "off" else ()
