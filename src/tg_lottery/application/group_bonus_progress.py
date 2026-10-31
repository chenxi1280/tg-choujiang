from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.base_activity_views import base_progress_text
from tg_lottery.application.daily_bonus import DailyBonusProgress, daily_bonus_progress
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import InvitationState, TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    DailyProgress,
    Invitation,
    Participant,
    TicketLedger,
)


@dataclass(frozen=True)
class InviteGroupProgress:
    chat_id: int
    valid_invites: int
    awarded_tickets: int


@dataclass(frozen=True)
class ActivityGroupProgress:
    chat_id: int
    valid_messages: int
    awarded_tickets: int
    total_awarded_tickets: int = 0
    qualified_days: int = 0


@dataclass(frozen=True)
class GroupBonusProgress:
    local_date: date
    invitations: tuple[InviteGroupProgress, ...]
    activities: tuple[ActivityGroupProgress, ...]
    base_progress: str = ""
    daily_bonus: DailyBonusProgress | None = None


async def load_group_bonus_progress(
    session_factory: async_sessionmaker[AsyncSession],
    campaign_id: UUID,
    user_id: int,
    *,
    observed_at: datetime | None = None,
) -> GroupBonusProgress | None:
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        participant = await session.scalar(
            select(Participant).where(
                Participant.campaign_id == campaign_id,
                Participant.user_id == user_id,
            )
        )
        if campaign is None or participant is None:
            return None
        rules = CampaignRules.model_validate(campaign.rules)
        local_date = (observed_at or utcnow()).astimezone(ZoneInfo(campaign.timezone)).date()
        group_ids = await _group_ids(session, campaign_id)
        invite_counts = await _valid_invite_counts(session, campaign_id, participant.id)
        ticket_totals = await _ticket_totals(session, campaign_id, participant.id)
        daily = await _daily_progress(session, campaign_id, participant.id, local_date=local_date)
        progress = _build_progress(
            rules,
            local_date=local_date,
            group_ids=group_ids,
            invite_counts=invite_counts,
            ticket_totals=ticket_totals,
            daily=daily,
            activity_days=await _activity_days(session, campaign_id, participant.id),
            base_text=await base_progress_text(session, campaign, participant),
        )

        if rules.daily_activity_bonus:
            state = await daily_bonus_progress(
                session, campaign, participant, observed_at=observed_at or utcnow()
            )
            progress = replace(progress, daily_bonus=state)
        return progress


async def _activity_days(
    session: AsyncSession, campaign_id: UUID, participant_id: UUID
) -> dict[UUID, int]:
    rows = await session.execute(
        select(DailyProgress.participation_group_id, func.count(DailyProgress.id))
        .where(
            DailyProgress.campaign_id == campaign_id,
            DailyProgress.participant_id == participant_id,
            DailyProgress.participation_group_id.is_not(None),
            DailyProgress.tickets_awarded > 0,
        )
        .group_by(DailyProgress.participation_group_id)
    )
    return {group_id: int(count) for group_id, count in rows}


async def _group_ids(session: AsyncSession, campaign_id: UUID) -> dict[int, UUID]:
    rows = await session.execute(
        select(
            CampaignParticipationGroup.chat_id_snapshot,
            CampaignParticipationGroup.id,
        ).where(CampaignParticipationGroup.campaign_id == campaign_id)
    )
    return {int(chat_id): group_id for chat_id, group_id in rows}


async def _valid_invite_counts(
    session: AsyncSession, campaign_id: UUID, participant_id: UUID
) -> dict[UUID, int]:
    rows = await session.execute(
        select(Invitation.participation_group_id, func.count(Invitation.id))
        .where(
            Invitation.campaign_id == campaign_id,
            Invitation.inviter_participant_id == participant_id,
            Invitation.state == InvitationState.VALID,
            Invitation.participation_group_id.is_not(None),
        )
        .group_by(Invitation.participation_group_id)
    )
    return {group_id: int(count) for group_id, count in rows if group_id is not None}


async def _ticket_totals(
    session: AsyncSession, campaign_id: UUID, participant_id: UUID
) -> dict[tuple[UUID, TicketSource], int]:
    rows = await session.execute(
        select(
            TicketLedger.participation_group_id,
            TicketLedger.source_type,
            func.sum(TicketLedger.delta),
        )
        .where(
            TicketLedger.campaign_id == campaign_id,
            TicketLedger.participant_id == participant_id,
            TicketLedger.participation_group_id.is_not(None),
        )
        .group_by(TicketLedger.participation_group_id, TicketLedger.source_type)
    )
    return {
        (group_id, source): int(total) for group_id, source, total in rows if group_id is not None
    }


async def _daily_progress(
    session: AsyncSession,
    campaign_id: UUID,
    participant_id: UUID,
    *,
    local_date: date,
) -> dict[UUID, DailyProgress]:
    rows = await session.scalars(
        select(DailyProgress).where(
            DailyProgress.campaign_id == campaign_id,
            DailyProgress.participant_id == participant_id,
            DailyProgress.local_date == local_date,
            DailyProgress.participation_group_id.is_not(None),
        )
    )
    return {
        row.participation_group_id: row for row in rows if row.participation_group_id is not None
    }


def _build_progress(
    rules: CampaignRules,
    *,
    local_date: date,
    group_ids: dict[int, UUID],
    invite_counts: dict[UUID, int],
    ticket_totals: dict[tuple[UUID, TicketSource], int],
    daily: dict[UUID, DailyProgress],
    activity_days: dict[UUID, int],
    base_text: str = "",
) -> GroupBonusProgress:
    invitations = []
    activities = []
    invite_ids = set(rules.group_invite_rules)
    if rules.admission and rules.admission.type == "invite":
        invite_ids.update(rules.admission.groups)
    for chat_id in invite_ids:
        if rules.invite_target_enabled(int(chat_id)):
            group_id = _required_group_id(group_ids, int(chat_id))
            awarded = sum(
                ticket_totals.get((group_id, source), 0)
                for source in (TicketSource.INVITATION, TicketSource.REVERSAL)
            )
            invitations.append(
                InviteGroupProgress(int(chat_id), invite_counts.get(group_id, 0), awarded)
            )
    for chat_id in rules.group_activity_rules:
        if rules.activity_rule_for(int(chat_id)) is not None:
            group_id = _required_group_id(group_ids, int(chat_id))
            progress = daily.get(group_id)
            activities.append(
                ActivityGroupProgress(
                    int(chat_id),
                    progress.valid_message_count if progress else 0,
                    progress.tickets_awarded if progress else 0,
                    ticket_totals.get((group_id, TicketSource.POPULARITY), 0),
                    activity_days.get(group_id, 0),
                )
            )
    return GroupBonusProgress(local_date, tuple(invitations), tuple(activities), base_text)


def _required_group_id(group_ids: dict[int, UUID], chat_id: int) -> UUID:
    group_id = group_ids.get(chat_id)
    if group_id is None:
        raise RuntimeError(f"group bonus rule has no participation group: {chat_id}")
    return group_id
