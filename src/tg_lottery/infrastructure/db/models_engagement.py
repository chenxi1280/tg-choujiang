from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.domain.enums import InvitationState, TicketSource
from tg_lottery.domain.public_refs import REFERRAL_PUBLIC_REF_LENGTH
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
    __table_args__ = (
        Index(
            "uq_daily_progress_group_date",
            "campaign_id",
            "participant_id",
            "local_date",
            "participation_group_id",
            unique=True,
            postgresql_where=text("participation_group_id IS NOT NULL"),
        ),
        Index(
            "uq_daily_progress_legacy_date",
            "campaign_id",
            "participant_id",
            "local_date",
            unique=True,
            postgresql_where=text("participation_group_id IS NULL"),
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    participation_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="RESTRICT"), index=True
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_message_counts: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    tickets_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_content_hash: Mapped[str | None] = mapped_column(String(64))
    last_content_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignReferralRoute(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "campaign_referral_routes"
    __table_args__ = (
        UniqueConstraint("public_ref", name="uq_campaign_referral_routes_public_ref"),
        UniqueConstraint(
            "campaign_id",
            "participant_id",
            "participation_group_id",
            name="uq_campaign_referral_routes_scope",
        ),
        CheckConstraint(
            "status IN ('active', 'join_only', 'cancelled', 'blocked')",
            name="referral_route_status",
        ),
    )

    public_ref: Mapped[str] = mapped_column(String(REFERRAL_PUBLIC_REF_LENGTH), nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    participation_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="RESTRICT"), index=True
    )
    bonus_join_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="active", server_default="active", nullable=False
    )


class InviteLink(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "invite_links"
    __table_args__ = (
        Index(
            "uq_invite_links_current_route",
            "referral_route_id",
            unique=True,
            postgresql_where=text("referral_route_id IS NOT NULL AND is_current"),
        ),
        Index(
            "uq_invite_links_route_version",
            "referral_route_id",
            "version",
            unique=True,
            postgresql_where=text("referral_route_id IS NOT NULL"),
        ),
        Index(
            "uq_invite_links_unrouted_group",
            "campaign_id",
            "participant_id",
            "participation_group_id",
            unique=True,
            postgresql_where=text(
                "participation_group_id IS NOT NULL AND referral_route_id IS NULL"
            ),
        ),
        Index(
            "uq_invite_links_legacy",
            "campaign_id",
            "participant_id",
            unique=True,
            postgresql_where=text("participation_group_id IS NULL"),
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    participation_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="RESTRICT"), index=True
    )
    referral_route_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_referral_routes.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    link_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    encrypted_url: Mapped[bytes | None] = mapped_column(LargeBinary)
    telegram_name: Mapped[str] = mapped_column(String(32), nullable=False)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(80))


class Invitation(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "invitations"
    __table_args__ = (
        Index(
            "uq_invitations_group_invitee",
            "campaign_id",
            "participation_group_id",
            "invitee_user_id",
            unique=True,
            postgresql_where=text("participation_group_id IS NOT NULL"),
        ),
        Index(
            "uq_invitations_legacy_invitee",
            "campaign_id",
            "invitee_user_id",
            unique=True,
            postgresql_where=text("participation_group_id IS NULL"),
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    invite_link_id: Mapped[UUID] = mapped_column(ForeignKey("invite_links.id", ondelete="RESTRICT"))
    inviter_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("participants.id", ondelete="RESTRICT")
    )
    participation_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="RESTRICT"), index=True
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
    final_reason: Mapped[str | None] = mapped_column(String(80))
    invitee_display_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    validation_observations: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)


class GroupMembershipFact(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "group_membership_facts"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_membership_facts_identity"),
        CheckConstraint(
            "last_observed_state IN ('member', 'left', 'unknown')",
            name="group_membership_state",
        ),
        CheckConstraint(
            "certainty IN ('confirmed', 'unknown')",
            name="group_membership_certainty",
        ),
    )

    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_observed_member_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    first_source_invite_link_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("invite_links.id", ondelete="SET NULL")
    )
    last_observed_state: Mapped[str] = mapped_column(String(16), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    certainty: Mapped[str] = mapped_column(String(16), nullable=False)


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


class UserGroupMessageTotal(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "user_group_message_totals"
    __table_args__ = (UniqueConstraint("user_id", "chat_id"),)

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_content_hash: Mapped[str | None] = mapped_column(String(64))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TicketLedger(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "ticket_ledger"

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    participation_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="RESTRICT"), index=True
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[TicketSource] = mapped_column(
        enum_type(TicketSource, "ticket_source"), nullable=False
    )
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    business_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)
