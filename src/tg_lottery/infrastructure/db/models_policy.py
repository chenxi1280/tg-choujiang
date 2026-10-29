from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.infrastructure.db.base import Base


class RuntimePolicy(Base):
    __tablename__ = "runtime_policies"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
