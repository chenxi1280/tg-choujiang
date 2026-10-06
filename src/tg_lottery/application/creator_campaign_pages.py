from dataclasses import dataclass

from sqlalchemy import func, select

from tg_lottery.application.campaign_outcomes import campaign_category_filter
from tg_lottery.application.creator_management_scope import cached_management_scope
from tg_lottery.application.creator_summary import AdminCampaignSummary, summary_in_session
from tg_lottery.infrastructure.db import Campaign

CREATOR_PAGE_SIZE = 6


@dataclass(frozen=True)
class CreatorCampaignPage:
    category: str
    page: int
    page_count: int
    total: int
    rows: tuple[Campaign, ...]
    managed: bool = False
    unconfirmed: int = 0
    summary: AdminCampaignSummary | None = None


async def load_creator_campaign_page(factory, user_id: int, *, options: dict):
    category = str(options.get("category", "active"))
    managed = bool(options.get("managed", False))
    statement = select(Campaign).where(campaign_category_filter(category))
    if managed:
        statement = statement.where(cached_management_scope(user_id))
    else:
        statement = statement.where(Campaign.created_by == user_id)
    async with factory() as session:
        unresolved = await _unconfirmed_total(session, user_id, managed)
        total = int(await session.scalar(select(func.count()).select_from(statement.subquery())))
        count = max(1, (total + CREATOR_PAGE_SIZE - 1) // CREATOR_PAGE_SIZE)
        page = min(count - 1, max(0, int(options.get("page", 0))))
        rows = await session.scalars(
            statement.order_by(Campaign.created_at.desc(), Campaign.id.desc())
            .offset(page * CREATOR_PAGE_SIZE)
            .limit(CREATOR_PAGE_SIZE)
        )
        return CreatorCampaignPage(
            category,
            page,
            count,
            total,
            tuple(rows),
            managed,
            int(unresolved or 0),
            None if managed else await summary_in_session(session, user_id),
        )


async def _unconfirmed_total(session, user_id, managed):
    statement = select(Campaign.id).where(campaign_category_filter("unconfirmed"))
    if managed:
        statement = statement.where(cached_management_scope(user_id))
    else:
        statement = statement.where(Campaign.created_by == user_id)
    return await session.scalar(select(func.count()).select_from(statement.subquery()))
