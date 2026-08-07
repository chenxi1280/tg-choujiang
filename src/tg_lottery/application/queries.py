from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.domain.enums import CampaignState, InvitationState, OutboxState, TicketSource
from tg_lottery.infrastructure.db import (
    Campaign,
    DrawRun,
    DrawSnapshot,
    Group,
    GroupAdmin,
    Invitation,
    InviteLink,
    Participant,
    Prize,
    TelegramOutbox,
    TicketLedger,
    Winner,
)


@dataclass(frozen=True)
class TicketSummary:
    campaign_name: str
    total: int
    popularity: int
    invitation: int


@dataclass(frozen=True)
class InvitationSummary:
    pending: int
    valid: int
    invalid: int


@dataclass(frozen=True)
class CampaignStats:
    participants: int
    valid_invitations: int
    pending_invitations: int
    tickets: int


@dataclass(frozen=True)
class DeliverySummary:
    confirmed: int
    pending: int
    failed: int
    unknown: int


@dataclass(frozen=True)
class WinnerRow:
    winner: Winner
    participant: Participant
    prize: Prize


class QueryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def admin_groups(self, user_id: int) -> list[Group]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Group)
                    .join(GroupAdmin, GroupAdmin.group_id == Group.id)
                    .where(GroupAdmin.user_id == user_id, GroupAdmin.active.is_(True))
                    .order_by(Group.title)
                )
            ).all()
            return list(rows)

    async def admin_campaigns(self, user_id: int) -> list[Campaign]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Campaign)
                    .join(GroupAdmin, GroupAdmin.group_id == Campaign.group_id)
                    .where(GroupAdmin.user_id == user_id, GroupAdmin.active.is_(True))
                    .order_by(Campaign.created_at.desc())
                    .limit(20)
                )
            ).all()
            return list(rows)

    async def campaign(self, campaign_id: UUID) -> Campaign | None:
        async with self._session_factory() as session:
            row = await session.get(Campaign, campaign_id)
            if row:
                session.expunge(row)
            return row

    async def campaign_group(self, campaign_id: UUID) -> Group:
        async with self._session_factory() as session:
            group = await session.scalar(
                select(Group).join(Campaign).where(Campaign.id == campaign_id)
            )
            if group is None:
                raise ValueError("campaign group not found")
            session.expunge(group)
            return group

    async def group_by_chat_id(self, chat_id: int) -> Group | None:
        async with self._session_factory() as session:
            group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
            if group:
                session.expunge(group)
            return group

    async def active_campaign_by_chat(self, chat_id: int) -> Campaign | None:
        async with self._session_factory() as session:
            campaign = await session.scalar(
                select(Campaign)
                .join(Group, Group.id == Campaign.group_id)
                .where(
                    Group.chat_id == chat_id,
                    Campaign.state.not_in(
                        [
                            CampaignState.DRAWN,
                            CampaignState.ARCHIVED,
                            CampaignState.CANCELLED,
                        ]
                    ),
                )
            )
            if campaign:
                session.expunge(campaign)
            return campaign

    async def prizes(self, campaign_id: UUID) -> list[Prize]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Prize).where(Prize.campaign_id == campaign_id).order_by(Prize.position)
                )
            ).all()
            return list(rows)

    async def ticket_summary(self, user_id: int) -> TicketSummary | None:
        async with self._session_factory() as session:
            participant = await session.scalar(
                select(Participant)
                .join(Campaign)
                .where(
                    Participant.user_id == user_id,
                    Campaign.state.not_in([CampaignState.ARCHIVED, CampaignState.CANCELLED]),
                )
                .order_by(Campaign.created_at.desc())
                .limit(1)
            )
            if participant is None:
                return None
            campaign = await session.get(Campaign, participant.campaign_id)
            totals = await self._ticket_totals(session, participant.id)
            return TicketSummary(
                campaign_name=campaign.name if campaign else "未知活动",
                total=sum(totals.values()),
                popularity=totals.get(TicketSource.POPULARITY, 0),
                invitation=totals.get(TicketSource.INVITATION, 0),
            )

    async def invitation_summary(self, user_id: int) -> InvitationSummary | None:
        async with self._session_factory() as session:
            participant = await session.scalar(
                select(Participant)
                .join(Campaign)
                .where(Participant.user_id == user_id)
                .order_by(Campaign.created_at.desc())
                .limit(1)
            )
            if participant is None:
                return None
            states = (
                await session.execute(
                    select(Invitation.state, func.count(Invitation.id))
                    .where(Invitation.inviter_participant_id == participant.id)
                    .group_by(Invitation.state)
                )
            ).all()
            counts = {state: int(count) for state, count in states}
            valid = counts.get(InvitationState.VALID, 0)
            pending = sum(
                counts.get(state, 0)
                for state in (
                    InvitationState.ATTRIBUTION_CANDIDATE,
                    InvitationState.JOINED_PENDING,
                    InvitationState.CHECKING,
                    InvitationState.VALIDATION_UNKNOWN,
                )
            )
            return InvitationSummary(pending, valid, sum(counts.values()) - pending - valid)

    async def invite_link_by_url(self, url: str) -> InviteLink | None:
        digest = sha256(url.encode()).hexdigest()
        async with self._session_factory() as session:
            link = await session.scalar(select(InviteLink).where(InviteLink.link_hash == digest))
            if link:
                session.expunge(link)
            return link

    async def invitation_for_chat_user(self, chat_id: int, user_id: int) -> Invitation | None:
        async with self._session_factory() as session:
            invitation = await session.scalar(
                select(Invitation)
                .join(Campaign, Campaign.id == Invitation.campaign_id)
                .join(Group, Group.id == Campaign.group_id)
                .where(Group.chat_id == chat_id, Invitation.invitee_user_id == user_id)
                .order_by(Invitation.created_at.desc())
                .limit(1)
            )
            if invitation:
                session.expunge(invitation)
            return invitation

    async def campaign_stats(self, campaign_id: UUID) -> CampaignStats:
        async with self._session_factory() as session:
            participants = await session.scalar(
                select(func.count(Participant.id)).where(Participant.campaign_id == campaign_id)
            )
            valid = await session.scalar(
                select(func.count(Invitation.id)).where(
                    Invitation.campaign_id == campaign_id,
                    Invitation.state == InvitationState.VALID,
                )
            )
            pending = await session.scalar(
                select(func.count(Invitation.id)).where(
                    Invitation.campaign_id == campaign_id,
                    Invitation.state.in_(
                        [
                            InvitationState.ATTRIBUTION_CANDIDATE,
                            InvitationState.JOINED_PENDING,
                            InvitationState.CHECKING,
                            InvitationState.VALIDATION_UNKNOWN,
                        ]
                    ),
                )
            )
            tickets = await session.scalar(
                select(func.coalesce(func.sum(TicketLedger.delta), 0)).where(
                    TicketLedger.campaign_id == campaign_id
                )
            )
            return CampaignStats(
                int(participants or 0),
                int(valid or 0),
                int(pending or 0),
                int(tickets or 0),
            )

    async def delivery_summary(self, campaign_id: UUID) -> DeliverySummary:
        async with self._session_factory() as session:
            aggregate_ids = await self._delivery_aggregate_ids(session, campaign_id)
            rows = (
                await session.execute(
                    select(TelegramOutbox.state, func.count(TelegramOutbox.id))
                    .where(TelegramOutbox.aggregate_id.in_(aggregate_ids))
                    .group_by(TelegramOutbox.state)
                )
            ).all()
            counts = {state: int(count) for state, count in rows}
            pending = sum(
                counts.get(state, 0)
                for state in (
                    OutboxState.PENDING,
                    OutboxState.CLAIMED,
                    OutboxState.RETRY_SCHEDULED,
                )
            )
            return DeliverySummary(
                confirmed=counts.get(OutboxState.CONFIRMED, 0),
                pending=pending,
                failed=counts.get(OutboxState.PRE_ACCEPT_FAILED, 0)
                + counts.get(OutboxState.CANCELLED, 0),
                unknown=counts.get(OutboxState.UNKNOWN_HOLD, 0),
            )

    async def _delivery_aggregate_ids(
        self, session: AsyncSession, campaign_id: UUID
    ) -> tuple[str, ...]:
        participant_ids = await session.scalars(
            select(Participant.id).where(Participant.campaign_id == campaign_id)
        )
        link_ids = await session.scalars(
            select(InviteLink.id).where(InviteLink.campaign_id == campaign_id)
        )
        draw_run_ids = await session.scalars(
            select(DrawRun.id)
            .join(DrawSnapshot, DrawSnapshot.id == DrawRun.snapshot_id)
            .where(DrawSnapshot.campaign_id == campaign_id)
        )
        winner_ids = await session.scalars(
            select(Winner.id)
            .join(DrawRun, DrawRun.id == Winner.draw_run_id)
            .join(DrawSnapshot, DrawSnapshot.id == DrawRun.snapshot_id)
            .where(DrawSnapshot.campaign_id == campaign_id)
        )
        identifiers = (campaign_id, *participant_ids, *link_ids, *draw_run_ids, *winner_ids)
        return tuple(str(identifier) for identifier in identifiers)

    async def winners(self, campaign_id: UUID) -> list[WinnerRow]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Winner, Participant, Prize)
                    .join(Participant, Participant.id == Winner.participant_id)
                    .join(Prize, Prize.id == Winner.prize_id)
                    .where(Prize.campaign_id == campaign_id)
                    .order_by(Winner.created_at)
                )
            ).all()
            return [WinnerRow(winner, participant, prize) for winner, participant, prize in rows]

    async def _ticket_totals(
        self, session: AsyncSession, participant_id: UUID
    ) -> dict[TicketSource, int]:
        rows = (
            await session.execute(
                select(TicketLedger.source_type, func.sum(TicketLedger.delta))
                .where(TicketLedger.participant_id == participant_id)
                .group_by(TicketLedger.source_type)
            )
        ).all()
        return {source: int(total) for source, total in rows}
