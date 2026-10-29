from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.featured_channel import promotion_state
from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import Campaign


async def featured_payload(session_factory, payload):
    ids = [UUID(item["campaign_id"]) for item in payload["items"]]
    async with session_factory() as session:
        campaigns = tuple(await session.scalars(select(Campaign).where(Campaign.id.in_(ids))))
    states = {str(c.id): promotion_state(c, utcnow()) for c in campaigns}
    if len(states) != len(set(ids)):
        raise ValueError("featured campaign disappeared")
    return payload | {
        "items": [item | {"state": states[item["campaign_id"]]} for item in payload["items"]]
    }
