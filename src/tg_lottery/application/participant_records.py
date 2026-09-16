from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.base_activity_views import base_progress_text
from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    QualificationState,
    TicketSource,
)
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignReferralRoute,
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
PARTICIPATED_CAMPAIGN_PAGE_SIZE = 10
ParticipationCategory = Literal["active", "pending", "ended"]
InvitationRecordFilter = Literal["all", "pending", "valid", "invalid"]


@dataclass(frozen=True)
class ParticipatedCampaign:
    public_ref: str
    name: str
    state: CampaignState
    qualification: QualificationState
    tickets: int
    draw_at: datetime
    timezone: str
    base_qualified: bool = True


@dataclass(frozen=True)
class ParticipatedCampaignPage:
    category: ParticipationCategory
    page: int
    total_pages: int
    rows: tuple[ParticipatedCampaign, ...]


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
    bonus_enabled: bool
    invite_enabled: bool
    draw_at: datetime
    timezone: str
    base_progress: str = ""
    base_qualified: bool = True


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
    draw_at: datetime
    group_chat_id: int | None
    group_title: str
    group_page: int
    status_filter: InvitationRecordFilter
    pending: int
    valid: int
    invalid: int
    page: int
    total_pages: int
    rows: tuple[InvitationRecordRow, ...]


@dataclass(frozen=True)
class _InvitationPageData:
    counts: Mapping[str, int]
    selected_page: int
    total_pages: int
    rows: tuple[InvitationRecordRow, ...]


async def load_participated_campaigns(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> list[ParticipatedCampaign]:
    async with session_factory() as session:
        rows = await session.execute(_participated_campaigns_statement(user_id))
    return [ParticipatedCampaign(*_participated_values(row)) for row in rows]


async def load_participated_campaign_page(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    category: ParticipationCategory,
    page: int,
    invitation_only: bool = False,
) -> ParticipatedCampaignPage:
    if page < 0:
        raise ValueError("participated campaign page cannot be negative")
    statement = _participated_campaigns_statement(user_id).where(
        Campaign.state.in_(_participation_category_states(category))
    )
    if invitation_only:
        statement = statement.where(_has_invitation_surface())
    async with session_factory() as session:
        total = int(
            await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        total_pages = max(
            1,
            (total + PARTICIPATED_CAMPAIGN_PAGE_SIZE - 1) // PARTICIPATED_CAMPAIGN_PAGE_SIZE,
        )
        selected_page = min(page, total_pages - 1)
        rows = await session.execute(
            statement.offset(selected_page * PARTICIPATED_CAMPAIGN_PAGE_SIZE).limit(
                PARTICIPATED_CAMPAIGN_PAGE_SIZE
            )
        )
    return ParticipatedCampaignPage(
        category=category,
        page=selected_page,
        total_pages=total_pages,
        rows=tuple(ParticipatedCampaign(*_participated_values(row)) for row in rows),
    )


def _participated_campaigns_statement(user_id: int):
    ticket_totals = (
        select(
            TicketLedger.participant_id.label("participant_id"),
            func.sum(TicketLedger.delta).label("tickets"),
        )
        .group_by(TicketLedger.participant_id)
        .subquery()
    )
    return (
        select(
            Campaign.public_ref,
            Campaign.name,
            Campaign.state,
            Participant.qualification_state,
            func.coalesce(ticket_totals.c.tickets, 0),
            Campaign.draw_at,
            Campaign.timezone,
            Participant.base_qualified,
        )
        .join(Participant, Participant.campaign_id == Campaign.id)
        .outerjoin(ticket_totals, ticket_totals.c.participant_id == Participant.id)
        .where(Participant.user_id == user_id)
        .order_by(Participant.created_at.desc())
    )


def _participation_category_states(
    category: ParticipationCategory,
) -> tuple[CampaignState, ...]:
    if category == "active":
        return (CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED)
    if category == "pending":
        return (
            CampaignState.SETTLING,
            CampaignState.READY_TO_DRAW,
            CampaignState.DRAWING,
        )
    if category == "ended":
        return (CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED)
    raise ValueError("参与记录分类无效")


def _has_invitation_surface():
    configured = Campaign.rules.op("@>")({"bonus_types": ["invite"]})
    legacy = Campaign.rules["luck_mode"].as_string().in_(("standard", "active"))
    has_route = exists(
        select(CampaignReferralRoute.id).where(
            CampaignReferralRoute.campaign_id == Campaign.id,
            CampaignReferralRoute.participant_id == Participant.id,
        )
    )
    has_record = exists(
        select(Invitation.id).where(
            Invitation.campaign_id == Campaign.id,
            Invitation.inviter_participant_id == Participant.id,
        )
    )
    return or_(configured, legacy, has_route, has_record)


def _participated_values(row: object) -> tuple[object, ...]:
    public_ref, name, state, qualification, tickets, draw_at, timezone, base_qualified = row
    return public_ref, name, state, qualification, int(tickets), draw_at, timezone, base_qualified


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
        rules = CampaignRules.model_validate(campaign.rules)
        return ParticipantCampaignDetail(
            base_progress=await base_progress_text(session, campaign, participant),
            base_qualified=participant.base_qualified,
            public_ref=campaign.public_ref,
            campaign_name=campaign.name,
            state=campaign.state,
            qualification=participant.qualification_state,
            total=sum(totals.values()),
            registration=totals.get(TicketSource.REGISTRATION, 0),
            activity=totals.get(TicketSource.POPULARITY, 0),
            invitation=totals.get(TicketSource.INVITATION, 0)
            + totals.get(TicketSource.REVERSAL, 0),
            bonus_enabled=bool(rules.bonus_types),
            invite_enabled=rules.invite_bonus_enabled,
            draw_at=campaign.draw_at,
            timezone=campaign.timezone,
        )


async def load_campaign_invitation_records(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    public_ref: str,
    *,
    page: int,
    chat_id: int | None = None,
    group_page: int = 0,
    status_filter: InvitationRecordFilter = "all",
) -> CampaignInvitationRecords | None:
    if page < 0 or group_page < 0:
        raise ValueError("invitation record pages cannot be negative")
    _validate_invitation_filter(status_filter)
    async with session_factory() as session:
        participant = await _participant_for_reference(session, user_id, public_ref)
        if participant is None:
            return None
        campaign, participant_id = participant
        target = await _record_target(session, campaign.id, chat_id)
        if chat_id is not None and target is None:
            return None
        group_id = target.id if target is not None else None
        page_data = await _load_invitation_page(
            session,
            participant_id,
            group_id=group_id,
            status_filter=status_filter,
            requested_page=page,
        )
        return CampaignInvitationRecords(
            public_ref=campaign.public_ref,
            campaign_name=campaign.name,
            timezone=campaign.timezone,
            draw_at=campaign.draw_at,
            group_chat_id=chat_id,
            group_title=target.title_snapshot if target is not None else "全部参与群",
            group_page=group_page,
            status_filter=status_filter,
            pending=page_data.counts["pending"],
            valid=page_data.counts["valid"],
            invalid=page_data.counts["invalid"],
            page=page_data.selected_page,
            total_pages=page_data.total_pages,
            rows=page_data.rows,
        )


async def _load_invitation_page(
    session: AsyncSession,
    participant_id: UUID,
    *,
    group_id: UUID | None,
    status_filter: InvitationRecordFilter,
    requested_page: int,
) -> _InvitationPageData:
    counts = await _invitation_counts(session, participant_id, group_id=group_id)
    total = await _filtered_invitation_count(
        session,
        participant_id,
        group_id=group_id,
        status_filter=status_filter,
    )
    total_pages = max(
        1,
        (total + INVITATION_RECORD_PAGE_SIZE - 1) // INVITATION_RECORD_PAGE_SIZE,
    )
    selected_page = min(requested_page, total_pages - 1)
    rows = await _invitation_rows(
        session,
        participant_id,
        selected_page,
        group_id=group_id,
        status_filter=status_filter,
    )
    return _InvitationPageData(
        counts=counts,
        selected_page=selected_page,
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


async def _invitation_rows(
    session: AsyncSession,
    participant_id: UUID,
    page: int,
    *,
    group_id: UUID | None,
    status_filter: InvitationRecordFilter,
) -> list[object]:
    statement = (
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
    statement = _scope_invitation_statement(
        statement,
        group_id=group_id,
        status_filter=status_filter,
    )
    rows = await session.execute(statement)
    return list(rows)


async def _invitation_counts(
    session: AsyncSession, participant_id: UUID, *, group_id: UUID | None
) -> dict[str, int]:
    statement = (
        select(Invitation.state, func.count(Invitation.id))
        .where(Invitation.inviter_participant_id == participant_id)
        .group_by(Invitation.state)
    )
    if group_id is not None:
        statement = statement.where(Invitation.participation_group_id == group_id)
    rows = await session.execute(statement)
    values = {state: int(count) for state, count in rows}
    pending = sum(values.get(state, 0) for state in _PENDING_INVITATION_STATES)
    valid = values.get(InvitationState.VALID, 0)
    return {"pending": pending, "valid": valid, "invalid": sum(values.values()) - pending - valid}


async def _filtered_invitation_count(
    session: AsyncSession,
    participant_id: UUID,
    *,
    group_id: UUID | None,
    status_filter: InvitationRecordFilter,
) -> int:
    statement = select(func.count(Invitation.id)).where(
        Invitation.inviter_participant_id == participant_id
    )
    statement = _scope_invitation_statement(
        statement,
        group_id=group_id,
        status_filter=status_filter,
    )
    return int(await session.scalar(statement) or 0)


def _scope_invitation_statement(
    statement: object,
    *,
    group_id: UUID | None,
    status_filter: InvitationRecordFilter,
):
    if group_id is not None:
        statement = statement.where(Invitation.participation_group_id == group_id)
    if status_filter == "pending":
        return statement.where(Invitation.state.in_(_PENDING_INVITATION_STATES))
    if status_filter == "valid":
        return statement.where(Invitation.state == InvitationState.VALID)
    if status_filter == "invalid":
        terminal = (*_PENDING_INVITATION_STATES, InvitationState.VALID)
        return statement.where(Invitation.state.not_in(terminal))
    return statement


async def _record_target(
    session: AsyncSession, campaign_id: UUID, chat_id: int | None
) -> CampaignParticipationGroup | None:
    if chat_id is None:
        return None
    return await session.scalar(
        select(CampaignParticipationGroup).where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            CampaignParticipationGroup.chat_id_snapshot == chat_id,
        )
    )


def _validate_invitation_filter(status_filter: str) -> None:
    if status_filter not in {"all", "pending", "valid", "invalid"}:
        raise ValueError("邀请记录筛选无效")


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
