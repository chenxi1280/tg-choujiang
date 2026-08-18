from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_query_targets import mandatory_chat_ids
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignReferralRoute,
    Invitation,
    InviteLink,
    Participant,
    TelegramOutbox,
)


@dataclass(frozen=True)
class GroupInviteLinkStatus:
    chat_id: int
    link: InviteLink | None
    delivery_state: OutboxState | None
    route: CampaignReferralRoute | None = None


@dataclass(frozen=True)
class ReferralLanding:
    route: CampaignReferralRoute
    campaign: Campaign
    target: CampaignParticipationGroup
    link: InviteLink | None


async def invite_link_by_url(
    session_factory: async_sessionmaker[AsyncSession], url: str
) -> InviteLink | None:
    digest = sha256(url.encode()).hexdigest()
    async with session_factory() as session:
        link = await session.scalar(select(InviteLink).where(InviteLink.link_hash == digest))
        if link:
            session.expunge(link)
        return link


async def invite_link_for_participant(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    campaign_id: UUID,
    user_id: int,
    chat_id: int | None,
) -> InviteLink | None:
    async with session_factory() as session:
        statement = (
            select(InviteLink)
            .join(Participant, Participant.id == InviteLink.participant_id)
            .where(
                InviteLink.campaign_id == campaign_id,
                Participant.campaign_id == campaign_id,
                Participant.user_id == user_id,
            )
        )
        if chat_id is not None:
            statement = statement.join(
                CampaignParticipationGroup,
                CampaignParticipationGroup.id == InviteLink.participation_group_id,
            ).where(CampaignParticipationGroup.chat_id_snapshot == chat_id)
        link = await session.scalar(statement)
        if link:
            session.expunge(link)
        return link


async def group_invite_link_statuses(
    session_factory: async_sessionmaker[AsyncSession], campaign_id: UUID, user_id: int
) -> dict[int, GroupInviteLinkStatus]:
    async with session_factory() as session:
        participant = await session.scalar(
            select(Participant).where(
                Participant.campaign_id == campaign_id,
                Participant.user_id == user_id,
            )
        )
        if participant is None:
            return {}
        targets = tuple(
            await session.scalars(
                select(CampaignParticipationGroup).where(
                    CampaignParticipationGroup.campaign_id == campaign_id
                )
            )
        )
        links = await _participant_group_links(session, participant.id)
        routes = await _participant_group_routes(session, participant.id)
        outboxes = await _participant_group_outboxes(session, participant.id, targets=targets)
        return {
            target.chat_id_snapshot: GroupInviteLinkStatus(
                chat_id=target.chat_id_snapshot,
                link=links.get(target.id),
                delivery_state=outboxes.get(target.id),
                route=routes.get(target.id),
            )
            for target in targets
        }


async def _participant_group_links(
    session: AsyncSession, participant_id: UUID
) -> dict[UUID, InviteLink]:
    rows = tuple(
        await session.scalars(
            select(InviteLink).where(
                InviteLink.participant_id == participant_id,
                InviteLink.is_current.is_(True),
            )
        )
    )
    for row in rows:
        session.expunge(row)
    return {row.participation_group_id: row for row in rows if row.participation_group_id}


async def _participant_group_routes(
    session: AsyncSession, participant_id: UUID
) -> dict[UUID, CampaignReferralRoute]:
    rows = tuple(
        await session.scalars(
            select(CampaignReferralRoute).where(
                CampaignReferralRoute.participant_id == participant_id
            )
        )
    )
    for row in rows:
        session.expunge(row)
    return {row.participation_group_id: row for row in rows}


async def referral_route_by_reference(
    session_factory: async_sessionmaker[AsyncSession], public_ref: str
) -> CampaignReferralRoute | None:
    async with session_factory() as session:
        route = await session.scalar(
            select(CampaignReferralRoute).where(CampaignReferralRoute.public_ref == public_ref)
        )
        if route is not None:
            session.expunge(route)
        return route


async def current_link_for_route(
    session_factory: async_sessionmaker[AsyncSession], route_id: UUID
) -> InviteLink | None:
    async with session_factory() as session:
        link = await session.scalar(
            select(InviteLink).where(
                InviteLink.referral_route_id == route_id,
                InviteLink.is_current.is_(True),
            )
        )
        if link is not None:
            session.expunge(link)
        return link


async def referral_landing_by_reference(
    session_factory: async_sessionmaker[AsyncSession], public_ref: str
) -> ReferralLanding | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(
                    CampaignReferralRoute,
                    Campaign,
                    CampaignParticipationGroup,
                    InviteLink,
                )
                .join(Campaign, Campaign.id == CampaignReferralRoute.campaign_id)
                .join(
                    CampaignParticipationGroup,
                    CampaignParticipationGroup.id == CampaignReferralRoute.participation_group_id,
                )
                .outerjoin(
                    InviteLink,
                    (InviteLink.referral_route_id == CampaignReferralRoute.id)
                    & InviteLink.is_current.is_(True),
                )
                .where(CampaignReferralRoute.public_ref == public_ref)
            )
        ).one_or_none()
        if row is None:
            return None
        route, campaign, target, link = row
        for item in (route, campaign, target, link):
            if item is not None:
                session.expunge(item)
        return ReferralLanding(route, campaign, target, link)


async def _participant_group_outboxes(
    session: AsyncSession,
    participant_id: UUID,
    *,
    targets: tuple[CampaignParticipationGroup, ...],
) -> dict[UUID, OutboxState]:
    target_ids = {target.id for target in targets}
    if not target_ids:
        return {}
    rows = await session.scalars(
        select(TelegramOutbox)
        .where(
            TelegramOutbox.aggregate_id == str(participant_id),
            TelegramOutbox.operation == "create_invite_link",
        )
        .order_by(TelegramOutbox.created_at)
    )
    states = {}
    for row in rows:
        raw_target = row.payload.get("participation_group_id")
        target_id = UUID(str(raw_target)) if raw_target else None
        if target_id in target_ids:
            states[target_id] = row.state
    return states


async def invitations_for_chat_user(
    session_factory: async_sessionmaker[AsyncSession], chat_id: int, user_id: int
) -> tuple[Invitation, ...]:
    async with session_factory() as session:
        rows = await _candidate_invitations(session, user_id)
        invitations = []
        for invitation, campaign, target_chat_id in rows:
            if not await _invitation_matches_chat(
                session,
                campaign=campaign,
                target_chat_id=target_chat_id,
                chat_id=chat_id,
            ):
                continue
            session.expunge(invitation)
            invitations.append(invitation)
        return tuple(invitations)


async def _invitation_matches_chat(
    session: AsyncSession,
    *,
    campaign: Campaign,
    target_chat_id: int | None,
    chat_id: int,
) -> bool:
    if target_chat_id is not None:
        return target_chat_id == chat_id
    return chat_id in await mandatory_chat_ids(session, campaign)


async def _candidate_invitations(session: AsyncSession, user_id: int) -> list[object]:
    rows = await session.execute(
        select(Invitation, Campaign, CampaignParticipationGroup.chat_id_snapshot)
        .join(Campaign, Campaign.id == Invitation.campaign_id)
        .outerjoin(
            CampaignParticipationGroup,
            CampaignParticipationGroup.id == Invitation.participation_group_id,
        )
        .where(
            Invitation.invitee_user_id == user_id,
            Campaign.state.in_(
                [
                    CampaignState.SCHEDULED,
                    CampaignState.RUNNING,
                    CampaignState.PAUSED,
                    CampaignState.SETTLING,
                ]
            ),
        )
        .order_by(Invitation.created_at.desc())
    )
    return rows.all()
