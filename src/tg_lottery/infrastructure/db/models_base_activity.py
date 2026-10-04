from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.infrastructure.db.base import Base, CreatedAtMixin, UuidPkMixin


class BaseActivityEvent(Base, UuidPkMixin, CreatedAtMixin):
    __tablename__ = "base_activity_events"
    __table_args__ = (
        UniqueConstraint("campaign_id", "chat_id", "message_id"),
        Index("ix_base_activity_participant_date", "participant_id", "chat_id", "local_date"),
    )
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    participation_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_participation_groups.id", ondelete="CASCADE")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_date: Mapped[date] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(Boolean)
    rejection_reason: Mapped[str | None] = mapped_column(String(64))
    source_update_id: Mapped[int | None] = mapped_column(BigInteger)
