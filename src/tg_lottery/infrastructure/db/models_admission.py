from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.infrastructure.db.base import Base, CreatedAtMixin, UuidPkMixin


class AdmissionProgress(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "admission_progress"
    __table_args__ = (UniqueConstraint("participant_id", "participation_group_id", "mode"),)

    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    participation_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(String(16))
    config_revision: Mapped[int] = mapped_column(Integer)
    qualified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_ref: Mapped[str] = mapped_column(String(128))
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)


class MessageTotalEvent(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "message_total_events"
    __table_args__ = (UniqueConstraint("chat_id", "message_id"),)

    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(Boolean)
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)
