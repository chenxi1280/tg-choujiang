from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_query_targets import (
    active_campaign_by_chat as load_active_campaign_by_chat,
)
from tg_lottery.application.campaign_query_targets import (
    active_campaigns_by_chat as load_active_campaigns_by_chat,
)
from tg_lottery.application.campaign_query_targets import (
    campaigns_for_mandatory_chat as load_campaigns_for_mandatory_chat,
)
from tg_lottery.application.campaign_query_targets import (
    load_campaign_targets,
)
from tg_lottery.application.campaign_references import resolve_campaign_reference
from tg_lottery.application.campaign_targets import QualificationTarget
from tg_lottery.application.group_bonus_progress import (
    GroupBonusProgress,
    load_group_bonus_progress,
)
from tg_lottery.application.participant_records import (
    CampaignInvitationRecords,
    InvitationRecordFilter,
    ParticipantCampaignDetail,
    ParticipatedCampaign,
    ParticipatedCampaignPage,
    ParticipationCategory,
    load_campaign_invitation_records,
    load_participant_campaign_detail,
    load_participated_campaign_page,
    load_participated_campaigns,
)
from tg_lottery.application.publication_queries import (
    PublicationIssue,
    load_publication_issues,
)
from tg_lottery.application.query_invites import (
    GroupInviteLinkStatus,
    ReferralLanding,
)
from tg_lottery.application.query_invites import (
    current_link_for_route as load_current_link_for_route,
)
from tg_lottery.application.query_invites import (
    group_invite_link_statuses as load_group_invite_link_statuses,
)
from tg_lottery.application.query_invites import (
    invitations_for_chat_user as load_invitations_for_chat_user,
)
from tg_lottery.application.query_invites import (
    invite_link_by_url as load_invite_link_by_url,
)
from tg_lottery.application.query_invites import (
    invite_link_for_participant as load_invite_link_for_participant,
)
from tg_lottery.application.query_invites import (
    referral_landing_by_reference as load_referral_landing_by_reference,
)
from tg_lottery.application.query_invites import (
    referral_route_by_reference as load_referral_route_by_reference,
)
from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    OutboxState,
    QualificationState,
)
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
class CampaignStats:
    participants: int
    valid_invitations: int
    pending_invitations: int
    tickets: int


@dataclass(frozen=True)
class AdminCampaignSummary:
    total: int
    active: int
    drawn: int
    cancelled: int


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

    async def user_ids_by_username(self, username: str) -> tuple[int, ...]:
        normalized = username.removeprefix("@").lower()
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(Participant.user_id)
                .where(func.lower(Participant.username) == normalized)
                .order_by(Participant.updated_at.desc())
                .limit(10)
            )
            return tuple(dict.fromkeys(int(user_id) for user_id in rows))

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

    async def admin_campaign_summary(self, user_id: int) -> AdminCampaignSummary:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Campaign.state, func.count(Campaign.id))
                    .where(Campaign.created_by == user_id)
                    .group_by(Campaign.state)
                )
            ).all()
        counts = {state: int(count) for state, count in rows}
        terminal = {CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED}
        return AdminCampaignSummary(
            total=sum(counts.values()),
            active=sum(count for state, count in counts.items() if state not in terminal),
            drawn=counts.get(CampaignState.DRAWN, 0) + counts.get(CampaignState.ARCHIVED, 0),
            cancelled=counts.get(CampaignState.CANCELLED, 0),
        )

    async def participated_campaigns(self, user_id: int) -> list[ParticipatedCampaign]:
        return await load_participated_campaigns(self._session_factory, user_id)

    async def participated_campaign_page(
        self,
        user_id: int,
        *,
        category: ParticipationCategory,
        page: int,
        invitation_only: bool = False,
    ) -> ParticipatedCampaignPage:
        return await load_participated_campaign_page(
            self._session_factory,
            user_id,
            category=category,
            page=page,
            invitation_only=invitation_only,
        )

    async def created_campaigns(self, user_id: int, category: str) -> list[Campaign]:
        states = _campaign_category_states(category)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Campaign)
                    .where(Campaign.created_by == user_id, Campaign.state.in_(states))
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

    async def campaign_by_reference(self, reference: str | UUID) -> Campaign | None:
        async with self._session_factory() as session:
            row = await resolve_campaign_reference(session, reference)
            if row:
                session.expunge(row)
            return row

    async def qualification_targets(self, campaign_id: UUID) -> tuple[QualificationTarget, ...]:
        async with self._session_factory() as session:
            return await load_campaign_targets(session, campaign_id)

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

    async def campaigns_for_mandatory_chat(self, chat_id: int) -> tuple[UUID, ...]:
        async with self._session_factory() as session:
            return await load_campaigns_for_mandatory_chat(session, chat_id)

    async def active_campaign_by_chat(self, chat_id: int) -> Campaign | None:
        async with self._session_factory() as session:
            campaign = await load_active_campaign_by_chat(session, chat_id)
            if campaign:
                session.expunge(campaign)
            return campaign

    async def active_campaigns_by_chat(self, chat_id: int) -> tuple[Campaign, ...]:
        async with self._session_factory() as session:
            campaigns = await load_active_campaigns_by_chat(session, chat_id)
            for campaign in campaigns:
                session.expunge(campaign)
            return campaigns

    async def prizes(self, campaign_id: UUID) -> list[Prize]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Prize).where(Prize.campaign_id == campaign_id).order_by(Prize.position)
                )
            ).all()
            return list(rows)

    async def participant_campaign_detail(
        self, user_id: int, public_ref: str
    ) -> ParticipantCampaignDetail | None:
        return await load_participant_campaign_detail(self._session_factory, user_id, public_ref)

    async def campaign_invitation_records(
        self,
        user_id: int,
        public_ref: str,
        *,
        page: int = 0,
        chat_id: int | None = None,
        group_page: int = 0,
        status_filter: InvitationRecordFilter = "all",
    ) -> CampaignInvitationRecords | None:
        return await load_campaign_invitation_records(
            self._session_factory,
            user_id,
            public_ref,
            page=page,
            chat_id=chat_id,
            group_page=group_page,
            status_filter=status_filter,
        )

    async def group_bonus_progress(
        self, campaign_id: UUID, user_id: int
    ) -> GroupBonusProgress | None:
        return await load_group_bonus_progress(self._session_factory, campaign_id, user_id)

    async def invite_link_by_url(self, url: str) -> InviteLink | None:
        return await load_invite_link_by_url(self._session_factory, url)

    async def invite_link_for_participant(
        self, campaign_id: UUID, user_id: int, chat_id: int | None = None
    ) -> InviteLink | None:
        return await load_invite_link_for_participant(
            self._session_factory,
            campaign_id=campaign_id,
            user_id=user_id,
            chat_id=chat_id,
        )

    async def group_invite_link_statuses(
        self, campaign_id: UUID, user_id: int
    ) -> dict[int, GroupInviteLinkStatus]:
        return await load_group_invite_link_statuses(self._session_factory, campaign_id, user_id)

    async def referral_route_by_reference(self, public_ref: str):
        return await load_referral_route_by_reference(self._session_factory, public_ref)

    async def current_link_for_route(self, route_id: UUID) -> InviteLink | None:
        return await load_current_link_for_route(self._session_factory, route_id)

    async def referral_landing_by_reference(self, public_ref: str) -> ReferralLanding | None:
        return await load_referral_landing_by_reference(self._session_factory, public_ref)

    async def invitations_for_chat_user(self, chat_id: int, user_id: int) -> tuple[Invitation, ...]:
        return await load_invitations_for_chat_user(self._session_factory, chat_id, user_id)

    async def campaign_stats(self, campaign_id: UUID) -> CampaignStats:
        async with self._session_factory() as session:
            participants = await session.scalar(
                select(func.count(Participant.id)).where(Participant.campaign_id == campaign_id)
            )
            valid = await session.scalar(
                select(func.count(Invitation.id)).where(
                    Invitation.campaign_id == campaign_id,
                    Invitation.state == InvitationState.VALID,
                    Invitation.left_at.is_(None),
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

    async def eligible_participant_count(self, campaign_id: UUID) -> int:
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count(Participant.id)).where(
                    Participant.campaign_id == campaign_id,
                    Participant.qualification_state == QualificationState.ELIGIBLE,
                )
            )
            return int(count or 0)

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

    async def publication_issues(self, campaign_id: UUID) -> tuple[PublicationIssue, ...]:
        return await load_publication_issues(self._session_factory, campaign_id)

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


def _campaign_category_states(category: str) -> tuple[CampaignState, ...]:
    if category == "drawn":
        return (CampaignState.DRAWN, CampaignState.ARCHIVED)
    if category == "cancelled":
        return (CampaignState.CANCELLED,)
    if category != "active":
        raise ValueError("抽奖分类无效")
    terminal = {CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED}
    return tuple(state for state in CampaignState if state not in terminal)
