from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ActivityRule(BaseModel, frozen=True):
    message_threshold: int = Field(default=10, ge=1)
    reward_tickets: int = Field(default=1, ge=1)
    daily_ticket_cap: int = Field(default=1, ge=1)
    min_text_length: int = Field(default=5, ge=1)
    duplicate_window_minutes: int = Field(default=5, ge=1)


class InviteRule(BaseModel, frozen=True):
    reward_tickets: int = Field(default=1, ge=1)
    retention_hours: int = Field(default=24, ge=1)
    require_activity: bool = False
    required_messages: int = Field(default=0, ge=0)


class CampaignRules(BaseModel, frozen=True):
    timezone: str = "Asia/Shanghai"
    activity: ActivityRule = ActivityRule()
    invite: InviteRule = InviteRule()
    required_channel_id: int | None = None
    required_chat_ids: tuple[int, ...] = ()
    username_required: bool = False
    allowed_regions: tuple[str, ...] = Field(default=(), max_length=3)
    draw_mode: Literal["fixed", "full"] = "fixed"
    participant_target: int | None = Field(default=None, ge=1)
    luck_mode: Literal["off", "standard", "active"] = "off"
    unique_winner: bool = True
    settlement_drain_seconds: int = Field(default=300, ge=0)


class CampaignDraft(BaseModel, frozen=True):
    group_id: int
    name: str = Field(min_length=1, max_length=120)
    preheat_at: datetime
    cutoff_at: datetime
    draw_at: datetime
    rules: CampaignRules = CampaignRules()
    presentation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timeline(self) -> CampaignDraft:
        if not self.preheat_at < self.cutoff_at < self.draw_at:
            raise ValueError("timeline must satisfy preheat_at < cutoff_at < draw_at")
        return self

    @model_validator(mode="after")
    def validate_draw_mode(self) -> CampaignDraft:
        if self.rules.draw_mode == "full" and self.rules.participant_target is None:
            raise ValueError("full draw mode requires participant_target")
        return self


def canonical_hash(value: dict[str, Any]) -> str:
    import json

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode()).hexdigest()


def invite_due_at(joined_at: datetime, rules: InviteRule) -> datetime:
    return joined_at + timedelta(hours=rules.retention_hours)
