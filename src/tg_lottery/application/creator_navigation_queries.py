from sqlalchemy import select

from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import AdminSession, Campaign


async def load_campaign_for_draft(factory, user_id: int, draft_id: str):
    async with factory() as session:
        return await session.scalar(
            select(Campaign).where(
                Campaign.created_by == user_id, Campaign.source_draft_id == draft_id
            )
        )


async def load_creator_navigation_state(session_factory, user_id: int) -> dict | None:
    async with session_factory() as session:
        row = await session.get(AdminSession, user_id)
        if row is None or row.expires_at <= utcnow() or not row.state.startswith("draft:"):
            return None
        data = dict(row.data) | {"draft_expires_at": row.expires_at.isoformat()}
        draft_id = data.get("draft_id")
        if draft_id:
            campaign = await session.scalar(
                select(Campaign).where(
                    Campaign.source_draft_id == draft_id, Campaign.created_by == user_id
                )
            )
            if campaign is not None:
                data = data | {
                    "published_campaign_id": str(campaign.id),
                    "published_campaign_ref": campaign.public_ref,
                }
        return data
