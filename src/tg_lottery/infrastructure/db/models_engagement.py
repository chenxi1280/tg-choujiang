from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.domain.enums import InvitationState, TicketSource
from tg_lottery.infrastructure.db.base import Base, CreatedAtMixin, TimestampMixin, UuidPkMixin
from tg_lottery.infrastructure.db.model_types import enum_type


class ActivityEvent(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "activity_events"
    __table_args__ = (UniqueConstraint("campaign_id", "chat_id", "message_id"),)

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(64))
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)


class DailyProgress(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "daily_progress"
    __table_args__ = (UniqueConstraint("campaign_id", "participant_id", "local_date"),)

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_message_counts: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    tickets_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_content_hash: Mapped[str | None] = mapped_column(String(64))
    last_content_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InviteLink(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "invite_links"
    __table_args__ = (UniqueConstraint("campaign_id", "participant_id"),)

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    link_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    encrypted_url: Mapped[bytes | None] = mapped_column(LargeBinary)
    telegram_name: Mapped[str] = mapped_column(String(32), nullable=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    remote_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "invitations"
    __table_args__ = (UniqueConstraint("campaign_id", "invitee_user_id"),)

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    invite_link_id: Mapped[UUID] = mapped_column(ForeignKey("invite_links.id", ondelete="RESTRICT"))
    inviter_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.id", ondelete="RESTRICT")
    )
    invitee_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[InvitationState] = mapped_column(
        enum_type(InvitationState, "invitation_state"), nullable=False
    )
    candidate_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    validation_error: Mapped[str | None] = mapped_column(String(120))
    validation_observations: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)


class InviteeActivityEvent(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "invitee_activity_events"
    __table_args__ = (UniqueConstraint("invitation_id", "chat_id", "message_id"),)

    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("invitations.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)


class TicketLedger(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "ticket_ledger"

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[TicketSource] = mapped_column(
        enum_type(TicketSource, "ticket_source"), nullable=False
    )
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    business_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)
