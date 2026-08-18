from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    QualificationState,
    TicketSource,
)
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Invitation,
    Participant,
    TicketLedger,
)

_PENDING_INVITATION_STATES = (
    InvitationState.ATTRIBUTION_CANDIDATE,
    InvitationState.JOINED_PENDING,
    InvitationState.CHECKING,
    InvitationState.VALIDATION_UNKNOWN,
)
INVITATION_RECORD_PAGE_SIZE = 10


@dataclass(frozen=True)
class ParticipatedCampaign:
    public_ref: str
    name: str
    state: CampaignState
    qualification: QualificationState
    tickets: int
    draw_at: datetime
    timezone: str


@dataclass(frozen=True)
class ParticipantCampaignDetail:
    public_ref: str
    campaign_name: str
    state: CampaignState
    qualification: QualificationState
    total: int
    registration: int
    activity: int
    invitation: int
    draw_at: datetime
    timezone: str


@dataclass(frozen=True)
class InvitationRecordRow:
    group_title: str
    invitee_user_id: int
    invitee_name: str | None
    state: InvitationState
    final_reason: str | None
    observed_at: datetime


@dataclass(frozen=True)
class CampaignInvitationRecords:
    public_ref: str
    campaign_name: str
    timezone: str
    pending: int
    valid: int
    invalid: int
    page: int
    total_pages: int
    rows: tuple[InvitationRecordRow, ...]


async def load_participated_campaigns(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> list[ParticipatedCampaign]:
    async with session_factory() as session:
        rows = await session.execute(_participated_campaigns_statement(user_id))
    return [ParticipatedCampaign(*_participated_values(row)) for row in rows]


def _participated_campaigns_statement(user_id: int):
    return (
        select(
            Campaign.public_ref,
            Campaign.name,
            Campaign.state,
            Participant.qualification_state,
            func.coalesce(func.sum(TicketLedger.delta), 0),
            Campaign.draw_at,
            Campaign.timezone,
        )
        .join(Participant, Participant.campaign_id == Campaign.id)
        .outerjoin(TicketLedger, TicketLedger.participant_id == Participant.id)
        .where(Participant.user_id == user_id)
        .group_by(Campaign.id, Participant.id)
        .order_by(Participant.created_at.desc())
        .limit(20)
    )


def _participated_values(row: object) -> tuple[object, ...]:
    public_ref, name, state, qualification, tickets, draw_at, timezone = row
    return public_ref, name, state, qualification, int(tickets), draw_at, timezone


async def load_participant_campaign_detail(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, public_ref: str
) -> ParticipantCampaignDetail | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Campaign, Participant)
                .join(Participant, Participant.campaign_id == Campaign.id)
                .where(Campaign.public_ref == public_ref, Participant.user_id == user_id)
            )
        ).one_or_none()
        if row is None:
            return None
        campaign, participant = row
        totals = await _ticket_totals(session, participant.id)
        return ParticipantCampaignDetail(
            public_ref=campaign.public_ref,
            campaign_name=campaign.name,
            state=campaign.state,
            qualification=participant.qualification_state,
            total=sum(totals.values()),
            registration=totals.get(TicketSource.REGISTRATION, 0),
            activity=totals.get(TicketSource.POPULARITY, 0),
            invitation=totals.get(TicketSource.INVITATION, 0)
            + totals.get(TicketSource.REVERSAL, 0),
            draw_at=campaign.draw_at,
            timezone=campaign.timezone,
        )


async def load_campaign_invitation_records(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    public_ref: str,
    *,
    page: int,
) -> CampaignInvitationRecords | None:
    if page < 0:
        raise ValueError("invitation record page cannot be negative")
    async with session_factory() as session:
        participant = await _participant_for_reference(session, user_id, public_ref)
        if participant is None:
            return None
        campaign, participant_id = participant
        counts = await _invitation_counts(session, participant_id)
        total = sum(counts.values())
        total_pages = max(
            1, (total + INVITATION_RECORD_PAGE_SIZE - 1) // INVITATION_RECORD_PAGE_SIZE
        )
        selected_page = min(page, total_pages - 1)
        rows = await _invitation_rows(session, participant_id, selected_page)
        return CampaignInvitationRecords(
            public_ref=campaign.public_ref,
            campaign_name=campaign.name,
            timezone=campaign.timezone,
            pending=counts["pending"],
            valid=counts["valid"],
            invalid=counts["invalid"],
            page=selected_page,
            total_pages=total_pages,
            rows=tuple(_invitation_record(row) for row in rows),
        )


async def _participant_for_reference(
    session: AsyncSession, user_id: int, public_ref: str
) -> tuple[Campaign, UUID] | None:
    row = (
        await session.execute(
            select(Campaign, Participant.id)
            .join(Participant, Participant.campaign_id == Campaign.id)
            .where(Campaign.public_ref == public_ref, Participant.user_id == user_id)
        )
    ).one_or_none()
    return row


async def _invitation_rows(session: AsyncSession, participant_id: UUID, page: int) -> list[object]:
    rows = await session.execute(
        select(Invitation, CampaignParticipationGroup.title_snapshot)
        .outerjoin(
            CampaignParticipationGroup,
            CampaignParticipationGroup.id == Invitation.participation_group_id,
        )
        .where(Invitation.inviter_participant_id == participant_id)
        .order_by(Invitation.created_at.desc())
        .offset(page * INVITATION_RECORD_PAGE_SIZE)
        .limit(INVITATION_RECORD_PAGE_SIZE)
    )
    return list(rows)


async def _invitation_counts(session: AsyncSession, participant_id: UUID) -> dict[str, int]:
    rows = await session.execute(
        select(Invitation.state, func.count(Invitation.id))
        .where(Invitation.inviter_participant_id == participant_id)
        .group_by(Invitation.state)
    )
    values = {state: int(count) for state, count in rows}
    pending = sum(values.get(state, 0) for state in _PENDING_INVITATION_STATES)
    valid = values.get(InvitationState.VALID, 0)
    return {"pending": pending, "valid": valid, "invalid": sum(values.values()) - pending - valid}


def _invitation_record(row: object) -> InvitationRecordRow:
    invitation, title = row
    return InvitationRecordRow(
        group_title=title or "历史参与群",
        invitee_user_id=invitation.invitee_user_id,
        invitee_name=invitation.invitee_display_name_snapshot,
        state=invitation.state,
        final_reason=invitation.final_reason,
        observed_at=invitation.joined_at or invitation.created_at,
    )


async def _ticket_totals(session: AsyncSession, participant_id: UUID) -> dict[TicketSource, int]:
    rows = await session.execute(
        select(TicketLedger.source_type, func.sum(TicketLedger.delta))
        .where(TicketLedger.participant_id == participant_id)
        .group_by(TicketLedger.source_type)
    )
    return {source: int(total) for source, total in rows}
