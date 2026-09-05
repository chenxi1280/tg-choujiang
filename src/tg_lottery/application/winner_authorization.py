from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db.models_ops import WinnerDesignationOperator


@dataclass(frozen=True)
class WinnerAuthorization:
    configured_user_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if any(type(user_id) is not int or user_id <= 0 for user_id in self.configured_user_ids):
            raise ValueError("winner operators must be positive Telegram user IDs")

    async def require(self, session: AsyncSession, actor_user_id: int) -> str:
        if type(actor_user_id) is not int or actor_user_id <= 0:
            raise UserFacingError("无权访问此运维入口: 操作人不在中奖指定授权名单中")
        if actor_user_id in self.configured_user_ids:
            return "config"
        # Hold the database grant until this operation commits; revocation affects the next call.
        operator = await session.scalar(
            select(WinnerDesignationOperator)
            .where(
                WinnerDesignationOperator.user_id == actor_user_id,
                WinnerDesignationOperator.enabled.is_(True),
            )
            .with_for_update(read=True)
        )
        if operator is None:
            raise UserFacingError("无权访问此运维入口: 操作人不在中奖指定授权名单中")
        return "database"
