from dataclasses import dataclass

from sqlalchemy import case, func, select

from tg_lottery.application.campaign_outcomes import (
    TERMINAL_CAMPAIGN_STATES,
    campaign_outcome_expression,
)
from tg_lottery.infrastructure.db import Campaign


@dataclass(frozen=True)
class AdminCampaignSummary:
    total: int
    active: int
    drawn: int
    cancelled: int
    unconfirmed: int = 0


async def summary_in_session(session, user_id):
    category = case(
        (Campaign.state.not_in(TERMINAL_CAMPAIGN_STATES), "active"),
        else_=campaign_outcome_expression(),
    )
    categories = select(category.label("category")).where(Campaign.created_by == user_id).subquery()
    rows = await session.execute(
        select(categories.c.category, func.count()).group_by(categories.c.category)
    )
    counts = {kind: int(count) for kind, count in rows}
    return AdminCampaignSummary(
        sum(counts.values()),
        counts.get("active", 0),
        counts.get("drawn", 0),
        counts.get("cancelled", 0),
        counts.get("unconfirmed", 0),
    )


async def load_creator_summary(factory, user_id):
    async with factory() as session:
        return await summary_in_session(session, user_id)
