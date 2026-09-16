from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

MAX_ACTIVITY_MESSAGE_THRESHOLD = 10_000
MAX_ACTIVITY_REWARD_TICKETS = 100
MAX_CHAT_MESSAGE_THRESHOLD = 500
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
    mode: Literal["ratio", "tiered"] = "tiered"
    reward_tickets: int = Field(default=1, ge=1)
    retention_hours: int = Field(default=24, ge=0)
    require_activity: bool = False
    required_messages: int = Field(default=0, ge=0)
    reward_tiers: tuple[InviteRewardTier, ...] = ()
    valid_invites_per_ticket: int | None = Field(default=None, ge=1)
    max_bonus_tickets: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_reward_tiers(self) -> InviteRule:
        if self.mode == "ratio":
            return self._validate_ratio()
        return self._validate_tiered()

    def _validate_tiered(self) -> InviteRule:
        if self.valid_invites_per_ticket is not None:
            raise ValueError("tiered invite rewards cannot set a ratio")
        counts = [tier.invite_count for tier in self.reward_tiers]
        totals = [tier.total_bonus for tier in self.reward_tiers]
        if counts != sorted(set(counts)) or totals != sorted(set(totals)):
            raise ValueError("invite reward tiers must increase strictly")
        if totals and self.max_bonus_tickets is None:
            raise ValueError("invite reward tiers require max bonus tickets")
        if totals and totals[-1] != self.max_bonus_tickets:
            raise ValueError("last invite reward tier must equal max bonus tickets")
        return self

    def _validate_ratio(self) -> InviteRule:
        if self.valid_invites_per_ticket is None:
            raise ValueError("ratio invite rewards require valid invites per ticket")
        if self.reward_tiers:
            raise ValueError("ratio invite rewards cannot set reward tiers")
        return self


class GroupInviteRule(BaseModel, frozen=True):
    enabled: bool = True
    invites_per_cycle: int = Field(default=2, ge=1)
    cap_mode: Literal["limited", "unlimited"] = "unlimited"
    max_bonus_tickets: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_cap(self) -> GroupInviteRule:
        _validate_cap(self.cap_mode, self.max_bonus_tickets, "invite")
        return self


class GroupActivityRule(BaseModel, frozen=True):
    enabled: bool = True
    messages_per_cycle: int = Field(default=10, ge=1)
    min_text_length: int = Field(default=5, ge=1)
    reward_tickets: int = Field(default=1, ge=1)
    daily_cap_mode: Literal["limited", "unlimited"] = "limited"
    daily_max_cycles: int | None = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_cap(self) -> GroupActivityRule:
        _validate_cap(self.daily_cap_mode, self.daily_max_cycles, "activity")
        return self


class BaseActivityRule(BaseModel, frozen=True):
    consecutive_days: int = Field(default=7, ge=1)
    daily_messages: int = Field(default=5, ge=1)
    min_text_length: int = Field(default=5, ge=1)


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
    base_activity_rules: dict[str, BaseActivityRule] = Field(default_factory=dict)
    message_thresholds: dict[str, int] = Field(default_factory=dict)
    group_invite_rules: dict[str, GroupInviteRule] = Field(default_factory=dict)
    group_activity_rules: dict[str, GroupActivityRule] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_message_thresholds(self) -> CampaignRules:
        for chat_id, threshold in self.message_thresholds.items():
            if not chat_id.lstrip("-").isdigit():
                raise ValueError("message threshold chat ids must be numeric")
            if not 1 <= threshold <= MAX_CHAT_MESSAGE_THRESHOLD:
                raise ValueError("chat message threshold out of range")
        return self

    @model_validator(mode="after")
    def validate_group_rule_keys(self) -> CampaignRules:
        for chat_id in (
            *self.group_invite_rules,
            *self.group_activity_rules,
            *self.base_activity_rules,
        ):
            if not chat_id.lstrip("-").isdigit():
                raise ValueError("group bonus rule chat ids must be numeric")
        return self

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

    def invite_rule_for(self, chat_id: int) -> GroupInviteRule | None:
        rule = self.group_invite_rules.get(str(chat_id))
        return rule if rule is not None and rule.enabled else None

    def activity_rule_for(self, chat_id: int) -> GroupActivityRule | None:
        rule = self.group_activity_rules.get(str(chat_id))
        return rule if rule is not None and rule.enabled else None


class CampaignDraft(BaseModel, frozen=True):
    group_id: int
    participation_group_ids: tuple[int, ...] = ()
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

    @model_validator(mode="after")
    def validate_participation_groups(self) -> CampaignDraft:
        group_ids = self.all_participation_group_ids
        _validate_group_identity(self.group_id, group_ids)
        _validate_requirement_scope(group_ids, self.rules, self.presentation)
        _validate_bonus_scope(group_ids, self.rules)
        return self

    @property
    def all_participation_group_ids(self) -> tuple[int, ...]:
        return self.participation_group_ids or (self.group_id,)


def _validate_group_identity(group_id: int, group_ids: tuple[int, ...]) -> None:
    if group_id not in group_ids:
        raise ValueError("compatibility group must be a participation group")
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("participation groups must be unique")


def _validate_requirement_scope(
    group_ids: tuple[int, ...], rules: CampaignRules, presentation: dict[str, Any]
) -> None:
    requirements = _requirement_ids(rules, presentation)
    overlap = requirements.intersection(group_ids)
    if overlap:
        raise ValueError(f"participation groups conflict with requirements: {sorted(overlap)}")


def _requirement_ids(rules: CampaignRules, presentation: dict[str, Any]) -> set[int]:
    requirements = set(rules.required_chat_ids)
    if rules.required_channel_id is not None:
        requirements.add(rules.required_channel_id)
    requirements.update(
        int(item["id"])
        for item in presentation.get("required_chats") or []
        if isinstance(item, dict) and item.get("id") is not None
    )
    return requirements


def _validate_bonus_scope(group_ids: tuple[int, ...], rules: CampaignRules) -> None:
    bonus_chat_ids = {
        int(value)
        for value in (
            *rules.group_invite_rules,
            *rules.group_activity_rules,
            *rules.base_activity_rules,
        )
    }
    unknown = bonus_chat_ids.difference(group_ids)
    if unknown:
        raise ValueError(f"group bonus rules require participation groups: {sorted(unknown)}")


def _validate_new_campaign_bonus_ranges(rules: CampaignRules) -> None:
    _validate_activity_bonus_range(rules)
    _validate_invite_bonus_range(rules)


def _validate_cap(mode: str, value: int | None, label: str) -> None:
    if mode == "limited" and value is None:
        raise ValueError(f"limited {label} bonus requires a cap")
    if mode == "unlimited" and value is not None:
        raise ValueError(f"unlimited {label} bonus cannot store a cap")


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
        rules.invite.valid_invites_per_ticket is not None
        and rules.invite.valid_invites_per_ticket > MAX_INVITE_COUNT
    ):
        raise ValueError("valid invites per ticket exceeds product limit")
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
