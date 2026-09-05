from sqlalchemy import BigInteger, CheckConstraint, String, true
from sqlalchemy.orm import Mapped, mapped_column

from tg_lottery.infrastructure.db.base import Base, TimestampMixin


class WinnerDesignationOperator(Base, TimestampMixin):
    __tablename__ = "winner_designation_operators"
    __table_args__ = (CheckConstraint("user_id > 0", name="user_id_positive"),)

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true(), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255))
