from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.domain.enums import DeliveryCertainty, JobState, OutboxState, PrizeClaimState
from tg_lottery.infrastructure.db.base import Base, CreatedAtMixin, TimestampMixin, UuidPkMixin
from tg_lottery.infrastructure.db.model_types import enum_type


class ScheduledJob(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "scheduled_jobs"

    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(80), nullable=False)
    business_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[JobState] = mapped_column(enum_type(JobState, "job_state"), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class DrawSnapshot(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "draw_snapshots"

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), unique=True
    )
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger_watermark: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prize_plan_hash: Mapped[str | None] = mapped_column(String(64))
    total_weight: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DrawEntry(Base, UuidPkMixin):
    __tablename__ = "draw_entries"
    __table_args__ = (UniqueConstraint("snapshot_id", "participant_id"),)

    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("draw_snapshots.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="RESTRICT"))
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    qualification_observation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("qualification_checks.id", ondelete="RESTRICT")
    )


class DrawRun(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "draw_runs"
    __table_args__ = (UniqueConstraint("snapshot_id", "algorithm_version"),)

    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("draw_snapshots.id", ondelete="RESTRICT"))
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    random_input: Mapped[str] = mapped_column(String(128), nullable=False)
    started_by: Mapped[int | None] = mapped_column(BigInteger)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DrawRound(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "draw_rounds"
    __table_args__ = (
        CheckConstraint("prize_slot_no > 0", name="ck_draw_rounds_prize_slot_positive"),
        UniqueConstraint("draw_run_id", "round_number"),
        UniqueConstraint(
            "draw_run_id", "prize_id", "prize_slot_no", name="uq_draw_rounds_prize_slot"
        ),
    )

    draw_run_id: Mapped[UUID] = mapped_column(ForeignKey("draw_runs.id", ondelete="CASCADE"))
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    prize_id: Mapped[UUID] = mapped_column(ForeignKey("prizes.id", ondelete="RESTRICT"))
    prize_slot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    range_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    range_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    selected_value: Mapped[int | None] = mapped_column(BigInteger)
    selected_participant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("participants.id", ondelete="RESTRICT")
    )
    undistributed: Mapped[bool] = mapped_column(default=False, nullable=False)


class Winner(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "winners"
    __table_args__ = (UniqueConstraint("draw_run_id", "draw_round_id"),)

    draw_run_id: Mapped[UUID] = mapped_column(ForeignKey("draw_runs.id", ondelete="CASCADE"))
    draw_round_id: Mapped[UUID] = mapped_column(ForeignKey("draw_rounds.id", ondelete="CASCADE"))
    prize_id: Mapped[UUID] = mapped_column(ForeignKey("prizes.id", ondelete="RESTRICT"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="RESTRICT"))
    claim_state: Mapped[PrizeClaimState] = mapped_column(
        enum_type(PrizeClaimState, "prize_claim_state"), default=PrizeClaimState.PENDING
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramOutbox(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "telegram_outbox"

    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(80), nullable=False)
    recipient_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    state: Mapped[OutboxState] = mapped_column(enum_type(OutboxState, "outbox_state"))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gateway_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_message_id: Mapped[int | None] = mapped_column(BigInteger)
    encrypted_remote_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class DeliveryAttempt(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "delivery_attempts"
    __table_args__ = (UniqueConstraint("outbox_id", "attempt_number"),)

    outbox_id: Mapped[UUID] = mapped_column(ForeignKey("telegram_outbox.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_method: Mapped[str] = mapped_column(String(80), nullable=False)
    certainty: Mapped[DeliveryCertainty] = mapped_column(
        enum_type(DeliveryCertainty, "delivery_certainty"), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_description: Mapped[str | None] = mapped_column(Text)
    remote_result: Mapped[dict[str, Any] | None]
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
