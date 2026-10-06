from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.campaign_outcomes import (
    campaign_category_filter,
    campaign_outcome_expression,
)
from tg_lottery.application.campaign_page_position import campaign_page_position
from tg_lottery.application.creator_management_scope import cached_management_scope
from tg_lottery.application.creator_summary import AdminCampaignSummary, summary_in_session
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import Campaign

CREATOR_PAGE_SIZE = 6


@dataclass(frozen=True)
class CreatorCampaignItem:
    id: UUID
    public_ref: str
    name: str
    state: CampaignState
    draw_at: datetime
    timezone: str
    outcome_kind: str


@dataclass(frozen=True)
class CreatorCampaignPage:
    category: str
    page: int
    page_count: int
    total: int
    rows: tuple[CreatorCampaignItem, ...]
    managed: bool = False
    unconfirmed: int = 0
    summary: AdminCampaignSummary | None = None
    anchor_missing: bool = False


async def load_creator_campaign_page(factory, user_id: int, *, options: dict):
    category = str(options.get("category", "active"))
    managed = bool(options.get("managed", False))
    statement = select(Campaign, campaign_outcome_expression()).where(
        creator_category_filter(category)
    )
    if managed:
        statement = statement.where(cached_management_scope(user_id))
    else:
        statement = statement.where(Campaign.created_by == user_id)
    async with factory() as session:
        unresolved = await _unconfirmed_total(session, user_id, managed)
        total = int(await session.scalar(select(func.count()).select_from(statement.subquery())))
        count = max(1, (total + CREATOR_PAGE_SIZE - 1) // CREATOR_PAGE_SIZE)
        ordering = (Campaign.created_at.desc(), Campaign.id.desc())
        located, missing = await campaign_page_position(
            session,
            statement,
            reference=options.get("anchor"),
            ordering=ordering,
            page_size=CREATOR_PAGE_SIZE,
            requested=int(options.get("page", 0)),
        )
        page = min(count - 1, max(0, located))
        rows = await session.execute(
            statement.order_by(*ordering).offset(page * CREATOR_PAGE_SIZE).limit(CREATOR_PAGE_SIZE)
        )
        return CreatorCampaignPage(
            category,
            page,
            count,
            total,
            tuple(_campaign_item(campaign, outcome) for campaign, outcome in rows),
            managed,
            int(unresolved or 0),
            None if managed else await summary_in_session(session, user_id),
            missing,
        )


async def _unconfirmed_total(session, user_id, managed):
    statement = select(Campaign.id).where(campaign_category_filter("unconfirmed"))
    if managed:
        statement = statement.where(cached_management_scope(user_id))
    else:
        statement = statement.where(Campaign.created_by == user_id)
    return await session.scalar(select(func.count()).select_from(statement.subquery()))


def creator_category_filter(category: str):
    states = {
        "publishing": (CampaignState.DRAFT, CampaignState.PUBLISHING),
        "active": (CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED),
        "pending": (CampaignState.SETTLING, CampaignState.READY_TO_DRAW, CampaignState.DRAWING),
        "ended": (CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED),
    }
    if category in states:
        return Campaign.state.in_(states[category])
    return campaign_category_filter(category)


def _campaign_item(campaign, outcome):
    return CreatorCampaignItem(
        campaign.id,
        campaign.public_ref,
        campaign.name,
        campaign.state,
        campaign.draw_at,
        campaign.timezone,
        outcome,
    )
