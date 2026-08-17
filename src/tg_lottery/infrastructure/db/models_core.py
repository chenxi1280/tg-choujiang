from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.domain.enums import CampaignState, QualificationState, UpdateState
from tg_lottery.domain.public_refs import CAMPAIGN_PUBLIC_REF_LENGTH
from tg_lottery.infrastructure.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UuidPkMixin,
)
from tg_lottery.infrastructure.db.model_types import enum_type


class BotRuntimeLease(Base):
    __tablename__ = "bot_runtime_leases"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    fence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PollingOffset(Base):
    __tablename__ = "polling_offsets"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_durable_update_id: Mapped[int | None] = mapped_column(BigInteger)
    last_poll_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    integrity_status: Mapped[str] = mapped_column(
        String(40), default="ok", server_default="ok", nullable=False
    )
    last_gap_from: Mapped[int | None] = mapped_column(BigInteger)
    last_gap_to: Mapped[int | None] = mapped_column(BigInteger)
    last_gap_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramUpdate(Base, TimestampMixin):
    __tablename__ = "telegram_updates"
    __table_args__ = (UniqueConstraint("bot_id", "update_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_type: Mapped[str] = mapped_column(String(40), nullable=False)
    routing_chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    routing_campaign_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any] | None]
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[UpdateState] = mapped_column(
        enum_type(UpdateState, "update_state"), default=UpdateState.RECEIVED, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Group(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "groups"

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    migrated_from_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    permission_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupAdmin(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "group_admins"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    region: Mapped[str | None] = mapped_column(String(32))
    region_selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    region_editable_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    region_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_draft: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    last_draft_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRegionChange(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "user_region_changes"

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    old_region: Mapped[str | None] = mapped_column(String(32))
    new_region: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Campaign(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("public_ref", name="uq_campaigns_public_ref"),
        UniqueConstraint("source_draft_id", name="uq_campaigns_source_draft_id"),
        Index("ix_campaigns_rules_gin", "rules", postgresql_using="gin"),
    )

    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"))
    public_ref: Mapped[str] = mapped_column(
        String(CAMPAIGN_PUBLIC_REF_LENGTH),
        nullable=False,
        server_default=text(
            "rtrim(translate(encode(uuid_send(gen_random_uuid()), 'base64'), '+/', '-_'), '=')"
        ),
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[CampaignState] = mapped_column(
        enum_type(CampaignState, "campaign_state"), default=CampaignState.DRAFT, nullable=False
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    preheat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    draw_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    config_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    presentation: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    prize_plan_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_draft_id: Mapped[str | None] = mapped_column(String(64))
    source_draft_revision: Mapped[int | None] = mapped_column(Integer)
    published_message_id: Mapped[int | None] = mapped_column(BigInteger)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contract_version: Mapped[str] = mapped_column(
        String(16), default="v1", server_default="v1", nullable=False
    )


class RuleRevision(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "rule_revisions"
    __table_args__ = (UniqueConstraint("campaign_id", "revision"),)

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    rules_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Prize(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "prizes"
    __table_args__ = (
        CheckConstraint("position > 0", name="ck_prizes_position_positive"),
        CheckConstraint("winner_count > 0", name="ck_prizes_winner_count_positive"),
        UniqueConstraint("campaign_id", "position", name="uq_prizes_campaign_position"),
    )

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    winner_count: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_instructions: Mapped[str] = mapped_column(Text, nullable=False)


class Participant(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id"),)

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    private_reachable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    qualification_state: Mapped[QualificationState] = mapped_column(
        enum_type(QualificationState, "qualification_state"), nullable=False
    )
    registered_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    registered_source_update_id: Mapped[int | None] = mapped_column(BigInteger)
    ticket_total_cache: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    registered_via_participation_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="SET NULL")
    )


class QualificationCheck(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "qualification_checks"
    __table_args__ = (
        UniqueConstraint(
            "participant_id",
            "phase",
            "source_update_id",
            name="uq_qualification_checks_source_update",
        ),
    )

    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[QualificationState] = mapped_column(
        enum_type(QualificationState, "qualification_check_state"), nullable=False
    )
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    observed_status: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(80))
    observations: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    participation_groups_state: Mapped[QualificationState | None] = mapped_column(
        enum_type(QualificationState, "qualification_participation_state")
    )
    requirements_state: Mapped[QualificationState | None] = mapped_column(
        enum_type(QualificationState, "qualification_requirements_state")
    )


class AdminSession(Base, TimestampMixin):
    __tablename__ = "admin_sessions"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CallbackToken(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "callback_tokens"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_by_update_id: Mapped[int | None] = mapped_column(BigInteger)


class AuditLog(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "audit_logs"

    actor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(80), nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)
