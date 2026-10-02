from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import (
    Campaign,
    DrawEntry,
    DrawRound,
    DrawRun,
    DrawSnapshot,
    Prize,
    Winner,
)


@dataclass(frozen=True)
class ParticipantAward:
    prize_position: int
    prize_name: str
    prize_slot_no: int
    claim_instructions: str


@dataclass(frozen=True)
class ParticipantDrawResult:
    completed_at: datetime
    frozen_tickets: int | None
    awards: tuple[ParticipantAward, ...]
    issuer_name: str
    issuer_user_id: int
    issuer_username: str | None


async def load_participant_draw_result(
    session: AsyncSession, campaign: Campaign, participant_id: UUID
) -> ParticipantDrawResult | None:
    if campaign.state not in {CampaignState.DRAWN, CampaignState.ARCHIVED}:
        return None
    row = (
        await session.execute(
            select(DrawSnapshot.id, DrawRun.id, DrawRun.completed_at)
            .join(DrawRun, DrawRun.snapshot_id == DrawSnapshot.id)
            .where(DrawSnapshot.campaign_id == campaign.id, DrawRun.completed_at.is_not(None))
        )
    ).one_or_none()
    if row is None:
        return None
    snapshot_id, draw_run_id, completed_at = row
    frozen_tickets = await session.scalar(
        select(DrawEntry.weight).where(
            DrawEntry.snapshot_id == snapshot_id, DrawEntry.participant_id == participant_id
        )
    )
    presentation = campaign.presentation or {}
    return ParticipantDrawResult(
        completed_at=completed_at,
        frozen_tickets=frozen_tickets,
        awards=await _participant_awards(session, draw_run_id, participant_id),
        issuer_name=str(presentation.get("issuer_name") or "活动创建者"),
        issuer_user_id=int(presentation.get("issuer_user_id") or campaign.created_by),
        issuer_username=presentation.get("issuer_username_at_publish"),
    )


async def _participant_awards(
    session: AsyncSession, draw_run_id: UUID, participant_id: UUID
) -> tuple[ParticipantAward, ...]:
    rows = await session.execute(
        select(Prize.position, Prize.name, DrawRound.prize_slot_no, Prize.claim_instructions)
        .join(Winner, Winner.prize_id == Prize.id)
        .join(DrawRound, DrawRound.id == Winner.draw_round_id)
        .where(Winner.draw_run_id == draw_run_id, Winner.participant_id == participant_id)
        .order_by(DrawRound.round_number)
    )
    return tuple(ParticipantAward(*row) for row in rows)
