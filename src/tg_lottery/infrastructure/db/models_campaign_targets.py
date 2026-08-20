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

from tg_lottery.infrastructure.db.base import Base, TimestampMixin, UuidPkMixin


class CampaignParticipationGroup(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "campaign_participation_groups"
    __table_args__ = (
        CheckConstraint("position > 0", name="position_positive"),
        UniqueConstraint("campaign_id", "group_id", name="uq_campaign_participation_group"),
        UniqueConstraint("campaign_id", "position", name="uq_campaign_participation_position"),
        Index(
            "uq_campaign_participation_primary",
            "campaign_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index(
            "ix_campaign_participation_active_group",
            "group_id",
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat_id_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    username_snapshot: Mapped[str | None] = mapped_column(String(64))
    private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    public_join_url: Mapped[str | None] = mapped_column(Text)
    encrypted_join_url: Mapped[bytes | None] = mapped_column(LargeBinary)
    join_url_hash: Mapped[str | None] = mapped_column(String(64))
    invite_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permission_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_state: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending", nullable=False
    )
    published_message_id: Mapped[int | None] = mapped_column(BigInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignRequirement(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "campaign_requirements"
    __table_args__ = (
        CheckConstraint("position > 0", name="position_positive"),
        UniqueConstraint("campaign_id", "chat_id_snapshot", name="uq_campaign_requirement_chat"),
        UniqueConstraint("campaign_id", "position", name="uq_campaign_requirement_position"),
    )

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    target_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("groups.id", ondelete="RESTRICT")
    )
    chat_id_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    chat_type: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    public_join_url: Mapped[str | None] = mapped_column(Text)
    encrypted_join_url: Mapped[bytes | None] = mapped_column(LargeBinary)
    join_url_hash: Mapped[str | None] = mapped_column(String(64))
    invite_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_threshold: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QualificationTargetObservation(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "qualification_target_observations"
    __table_args__ = (
        UniqueConstraint(
            "check_id",
            "target_kind",
            "chat_id",
            "attempt_number",
            name="uq_qualification_target_attempt",
        ),
    )

    check_id: Mapped[UUID] = mapped_column(
        ForeignKey("qualification_checks.id", ondelete="CASCADE"), index=True
    )
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), index=True
    )
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_status: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CampaignOperationalBlock(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "campaign_operational_blocks"
    __table_args__ = (
        Index(
            "uq_campaign_operational_block_active",
            "campaign_id",
            "target_kind",
            "chat_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error_code: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), default="telegram_outbox", server_default="telegram_outbox", nullable=False
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    details: Mapped[str | None] = mapped_column(Text)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
