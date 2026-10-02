from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import String, case, cast, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import AuditLog, Campaign, DrawRun, DrawSnapshot

CampaignOutcomeKind = Literal["drawn", "cancelled", "unconfirmed"]
TERMINAL_CAMPAIGN_STATES = (
    CampaignState.DRAWN,
    CampaignState.ARCHIVED,
    CampaignState.CANCELLED,
)


@dataclass(frozen=True)
class CampaignOutcomeFacts:
    kind: CampaignOutcomeKind
    snapshot_id: UUID | None = None
    draw_run_id: UUID | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


async def load_campaign_outcome(session: AsyncSession, campaign_id: UUID) -> CampaignOutcomeFacts:
    completed = (
        await session.execute(
            select(DrawSnapshot.id, DrawRun.id, DrawRun.completed_at)
            .join(DrawRun, DrawRun.snapshot_id == DrawSnapshot.id)
            .where(DrawSnapshot.campaign_id == campaign_id, DrawRun.completed_at.is_not(None))
        )
    ).one_or_none()
    if completed is not None:
        return CampaignOutcomeFacts(
            kind="drawn",
            snapshot_id=completed[0],
            draw_run_id=completed[1],
            completed_at=completed[2],
        )
    cancelled_at = await session.scalar(
        select(AuditLog.created_at)
        .where(
            AuditLog.aggregate_type == "campaign",
            AuditLog.aggregate_id == str(campaign_id),
            AuditLog.action == "campaign.cancelled",
        )
        .order_by(AuditLog.created_at, AuditLog.id)
        .limit(1)
    )
    if cancelled_at is not None:
        return CampaignOutcomeFacts(kind="cancelled", cancelled_at=cancelled_at)
    return CampaignOutcomeFacts(kind="unconfirmed")


def campaign_outcome_expression() -> ColumnElement[str]:
    completed = (
        select(DrawRun.id)
        .join(DrawSnapshot, DrawSnapshot.id == DrawRun.snapshot_id)
        .where(DrawSnapshot.campaign_id == Campaign.id, DrawRun.completed_at.is_not(None))
        .correlate(Campaign)
        .exists()
    )
    cancelled = (
        select(AuditLog.id)
        .where(
            AuditLog.aggregate_type == "campaign",
            AuditLog.aggregate_id == cast(Campaign.id, String),
            AuditLog.action == "campaign.cancelled",
        )
        .correlate(Campaign)
        .exists()
    )
    return case((completed, "drawn"), (cancelled, "cancelled"), else_="unconfirmed")


def campaign_category_filter(category: str) -> ColumnElement[bool]:
    if category == "active":
        return Campaign.state.not_in(TERMINAL_CAMPAIGN_STATES)
    if category not in {"drawn", "cancelled", "unconfirmed"}:
        raise ValueError("抽奖分类无效")
    return Campaign.state.in_(TERMINAL_CAMPAIGN_STATES) & (
        campaign_outcome_expression() == category
    )
