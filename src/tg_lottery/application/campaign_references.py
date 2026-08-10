from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.domain.public_refs import is_campaign_public_ref
from tg_lottery.infrastructure.db import Campaign


async def resolve_campaign_reference(
    session: AsyncSession,
    reference: str | UUID,
) -> Campaign | None:
    value = str(reference)
    if is_campaign_public_ref(value):
        return await session.scalar(select(Campaign).where(Campaign.public_ref == value))
    try:
        campaign_id = UUID(value)
    except ValueError:
        return None
    return await session.get(Campaign, campaign_id)
