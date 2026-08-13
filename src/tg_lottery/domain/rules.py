from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

MAX_ACTIVITY_MESSAGE_THRESHOLD = 10_000
MAX_ACTIVITY_REWARD_TICKETS = 100
MAX_INVITE_COUNT = 100_000
MAX_INVITE_BONUS_TICKETS = 10_000


class ActivityRule(BaseModel, frozen=True):
    message_threshold: int = Field(default=10, ge=1)
    reward_tickets: int = Field(default=1, ge=1)
    daily_ticket_cap: int = Field(default=1, ge=1)
    min_text_length: int = Field(default=5, ge=1)
    duplicate_window_minutes: int = Field(default=5, ge=1)
    settle_once_per_day: bool = False


class InviteRewardTier(BaseModel, frozen=True):
    invite_count: int = Field(ge=1)
    total_bonus: int = Field(ge=1)


class InviteRule(BaseModel, frozen=True):
    reward_tickets: int = Field(default=1, ge=1)
    retention_hours: int = Field(default=24, ge=1)
    require_activity: bool = False
    required_messages: int = Field(default=0, ge=0)
    reward_tiers: tuple[InviteRewardTier, ...] = ()
    max_bonus_tickets: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_reward_tiers(self) -> InviteRule:
        counts = [tier.invite_count for tier in self.reward_tiers]
        totals = [tier.total_bonus for tier in self.reward_tiers]
        if counts != sorted(set(counts)) or totals != sorted(set(totals)):
            raise ValueError("invite reward tiers must increase strictly")
        if totals and self.max_bonus_tickets is None:
            raise ValueError("invite reward tiers require max bonus tickets")
        if totals and totals[-1] != self.max_bonus_tickets:
            raise ValueError("last invite reward tier must equal max bonus tickets")
        return self


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
    bonus_types: tuple[Literal["invite", "activity"], ...] = ()
    unique_winner: bool = True
    settlement_drain_seconds: int = Field(default=300, ge=0)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_bonus_mode(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "bonus_types" in value:
            return value
        enabled = value.get("luck_mode", "off") != "off"
        return dict(value) | {"bonus_types": ("invite", "activity") if enabled else ()}

    @property
    def invite_bonus_enabled(self) -> bool:
        return "invite" in self.bonus_types

    @property
    def activity_bonus_enabled(self) -> bool:
        return "activity" in self.bonus_types


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

    @model_validator(mode="after")
    def validate_bonus_ranges(self) -> CampaignDraft:
        _validate_new_campaign_bonus_ranges(self.rules)
        return self


def _validate_new_campaign_bonus_ranges(rules: CampaignRules) -> None:
    _validate_activity_bonus_range(rules)
    _validate_invite_bonus_range(rules)


def _validate_activity_bonus_range(rules: CampaignRules) -> None:
    if not rules.activity_bonus_enabled:
        return
    if rules.activity.message_threshold > MAX_ACTIVITY_MESSAGE_THRESHOLD:
        raise ValueError("activity message threshold exceeds product limit")
    if rules.activity.reward_tickets > MAX_ACTIVITY_REWARD_TICKETS:
        raise ValueError("activity reward tickets exceed product limit")
    if rules.activity.daily_ticket_cap > MAX_ACTIVITY_REWARD_TICKETS:
        raise ValueError("activity daily ticket cap exceeds product limit")


def _validate_invite_bonus_range(rules: CampaignRules) -> None:
    if not rules.invite_bonus_enabled:
        return
    if any(tier.invite_count > MAX_INVITE_COUNT for tier in rules.invite.reward_tiers):
        raise ValueError("invite count exceeds product limit")
    if rules.invite.reward_tickets > MAX_INVITE_BONUS_TICKETS:
        raise ValueError("invite reward tickets exceed product limit")
    if (
        rules.invite.max_bonus_tickets is not None
        and rules.invite.max_bonus_tickets > MAX_INVITE_BONUS_TICKETS
    ):
        raise ValueError("invite bonus tickets exceed product limit")


def canonical_hash(value: dict[str, Any]) -> str:
    import json

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode()).hexdigest()


def invite_due_at(joined_at: datetime, rules: InviteRule) -> datetime:
    return joined_at + timedelta(hours=rules.retention_hours)
