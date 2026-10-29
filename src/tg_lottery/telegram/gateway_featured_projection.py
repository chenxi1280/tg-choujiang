from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.featured_channel import promotion_state
from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import Campaign, DrawRun, DrawSnapshot


async def featured_payload(session_factory, payload):
    ids = [UUID(item["campaign_id"]) for item in payload["items"]]
    async with session_factory() as session:
        campaigns = tuple(await session.scalars(select(Campaign).where(Campaign.id.in_(ids))))
        completed = dict(
            (
                await session.execute(
                    select(DrawSnapshot.campaign_id, func.max(DrawRun.completed_at))
                    .join(DrawRun, DrawRun.snapshot_id == DrawSnapshot.id)
                    .where(DrawSnapshot.campaign_id.in_(ids))
                    .group_by(DrawSnapshot.campaign_id)
                )
            ).all()
        )
    current = {
        str(c.id): {
            "state": promotion_state(c, utcnow()),
            "cutoff_at": c.cutoff_at.isoformat(),
            "draw_at": c.draw_at.isoformat(),
            "completed_at": completed[c.id].isoformat() if completed.get(c.id) else None,
        }
        for c in campaigns
    }
    if len(current) != len(set(ids)):
        raise ValueError("featured campaign disappeared")
    return payload | {"items": [item | current[item["campaign_id"]] for item in payload["items"]]}
