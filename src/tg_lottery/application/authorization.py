from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.infrastructure.db import GroupAdmin


async def require_group_admin(
    session: AsyncSession,
    *,
    group_id: UUID,
    user_id: int,
) -> None:
    statement = select(GroupAdmin.id).where(
        GroupAdmin.group_id == group_id,
        GroupAdmin.user_id == user_id,
        GroupAdmin.active.is_(True),
    )
    if await session.scalar(statement) is None:
        raise AuthorizationDenied("user is not an active administrator of this group")
