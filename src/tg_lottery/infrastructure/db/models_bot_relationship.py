from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.infrastructure.db.base import Base


class BotRelationship(Base):
    __tablename__ = "bot_relationships"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_update_id: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_update_id: Mapped[int | None] = mapped_column(BigInteger)
    blocked: Mapped[bool | None] = mapped_column(Boolean)
    conflicted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
