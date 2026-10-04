from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, func, or_, select

from tg_lottery.application.admission_qualification import behavior_clause
from tg_lottery.application.base_activity_notifications import notify_unqualified_at_cutoff
from tg_lottery.application.campaign_targets import qualification_targets
from tg_lottery.application.draw_commitments import (
    ensure_draw_commitment,
    require_commitment_confirmed,
)
from tg_lottery.application.operational_blocks import has_active_campaign_block
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    QualificationState,
    UpdateState,
)
from tg_lottery.domain.errors import SettlementBlocked
from tg_lottery.domain.rules import CampaignRules, canonical_hash
from tg_lottery.domain.transitions import require_campaign_transition
from tg_lottery.infrastructure.db import (
    Campaign,
    DrawEntry,
    DrawSnapshot,
    Group,
    Invitation,
    Participant,
    PollingOffset,
    Prize,
    QualificationCheck,
    TelegramUpdate,
    TicketLedger,
)

_PENDING_UPDATE_STATES = (
    UpdateState.RECEIVED,
    UpdateState.PROCESSING,
    UpdateState.FAILED,
)
_PENDING_INVITATION_STATES = (
    InvitationState.JOINED_PENDING,
    InvitationState.CHECKING,
    InvitationState.VALIDATION_UNKNOWN,
)


class SettlementService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def settle(self, campaign_id: UUID, now: datetime) -> DrawSnapshot:
        async with self._uow_factory() as uow:
            campaign = await uow.session.scalar(
                select(Campaign).where(Campaign.id == campaign_id).with_for_update()
            )
            if campaign is None:
                raise ValueError("campaign not found")
            existing = await uow.session.scalar(
                select(DrawSnapshot).where(DrawSnapshot.campaign_id == campaign.id)
            )
            if existing:
                return existing
            self._enter_settling(campaign)
            await ensure_draw_commitment(uow.session, campaign, now)
            await self._expire_unqualified_invitations(uow.session, campaign)
            await self._require_drain_complete(uow.session, campaign, now)
            await notify_unqualified_at_cutoff(uow.session, campaign)
            entries = await self._eligible_entries(uow.session, campaign.id)
            snapshot = await self._create_snapshot(
                uow.session,
                campaign=campaign,
                entries=entries,
                now=now,
            )
            campaign.state = CampaignState.READY_TO_DRAW
            campaign.settled_at = now
            return snapshot

    def _enter_settling(self, campaign: Campaign) -> None:
        if campaign.state in {CampaignState.RUNNING, CampaignState.PAUSED}:
            require_campaign_transition(campaign.state, CampaignState.SETTLING)
            campaign.state = CampaignState.SETTLING
        if campaign.state is not CampaignState.SETTLING:
            raise SettlementBlocked(f"campaign state {campaign.state} cannot settle")

    async def _expire_unqualified_invitations(self, session: object, campaign: Campaign) -> None:
        invitations = (
            await session.scalars(
                select(Invitation)
                .where(
                    Invitation.campaign_id == campaign.id,
                    Invitation.state.in_(
                        [
                            InvitationState.ATTRIBUTION_CANDIDATE,
                            InvitationState.JOINED_PENDING,
                            InvitationState.CHECKING,
                        ]
                    ),
                )
                .with_for_update()
            )
        ).all()
        for invitation in invitations:
            if invitation.due_at is None or invitation.due_at > campaign.cutoff_at:
                invitation.state = InvitationState.EXPIRED_UNQUALIFIED

    async def _require_drain_complete(
        self, session: object, campaign: Campaign, now: datetime
    ) -> None:
        rules = CampaignRules.model_validate(campaign.rules)
        drain_until = campaign.cutoff_at + timedelta(seconds=rules.settlement_drain_seconds)
        blockers: list[str] = []
        if now < drain_until:
            blockers.append("drain_window_not_elapsed")
        if await self._has_pending_updates(session, campaign):
            blockers.append("inbox_pending_before_cutoff")
        if await self._has_pending_invitations(session, campaign.id):
            blockers.append("invitation_validation_pending")
        if await self._has_pending_qualifications(session, campaign.id):
            blockers.append("qualification_review_pending")
        if await has_active_campaign_block(session, campaign.id):
            blockers.append("campaign_operational_blocked")
        try:
            await require_commitment_confirmed(session, campaign)
        except SettlementBlocked as exc:
            blockers.append(str(exc))
        if not await self._polling_fresh_after_cutoff(session, campaign.cutoff_at):
            blockers.append("polling_not_fresh_after_cutoff")
        if blockers:
            raise SettlementBlocked(",".join(blockers))

    async def _has_pending_updates(self, session: object, campaign: Campaign) -> bool:
        targets = await qualification_targets(session, campaign)
        chat_ids = tuple(target.chat_id for target in targets)
        if not chat_ids:
            chat_id = await session.scalar(
                select(Group.chat_id).where(Group.id == campaign.group_id)
            )
            if chat_id is None:
                raise SettlementBlocked("campaign_group_missing")
            chat_ids = (chat_id,)
        update_id = await session.scalar(
            select(TelegramUpdate.id)
            .where(
                TelegramUpdate.event_at <= campaign.cutoff_at,
                TelegramUpdate.state.in_(_PENDING_UPDATE_STATES),
                or_(
                    TelegramUpdate.routing_campaign_id == str(campaign.id),
                    TelegramUpdate.routing_chat_id.in_(chat_ids),
                ),
            )
            .limit(1)
        )
        return update_id is not None

    async def _has_pending_invitations(self, session: object, campaign_id: UUID) -> bool:
        invitation_id = await session.scalar(
            select(Invitation.id)
            .where(
                Invitation.campaign_id == campaign_id,
                Invitation.state.in_(_PENDING_INVITATION_STATES),
            )
            .limit(1)
        )
        return invitation_id is not None

    async def _has_pending_qualifications(self, session: object, campaign_id: UUID) -> bool:
        draw_check_exists = exists(
            select(QualificationCheck.id).where(
                QualificationCheck.participant_id == Participant.id,
                QualificationCheck.phase == "draw",
            )
        )
        participant_id = await session.scalar(
            select(Participant.id)
            .where(
                Participant.campaign_id == campaign_id,
                (
                    Participant.qualification_state.in_(
                        [QualificationState.PENDING, QualificationState.UNKNOWN]
                    )
                    | ~draw_check_exists
                ),
            )
            .limit(1)
        )
        return participant_id is not None

    async def _polling_fresh_after_cutoff(self, session: object, cutoff_at: datetime) -> bool:
        row = (
            await session.execute(
                select(
                    func.max(PollingOffset.last_poll_succeeded_at),
                    func.count(PollingOffset.bot_id).filter(PollingOffset.integrity_status != "ok"),
                )
            )
        ).one()
        last_poll, integrity_issues = row
        return last_poll is not None and last_poll >= cutoff_at and int(integrity_issues or 0) == 0

    async def _eligible_entries(
        self, session: object, campaign_id: UUID
    ) -> list[tuple[Participant, int]]:
        statement = (
            select(Participant, func.coalesce(func.sum(TicketLedger.delta), 0))
            .outerjoin(TicketLedger, TicketLedger.participant_id == Participant.id)
            .where(
                Participant.campaign_id == campaign_id,
                Participant.qualification_state == QualificationState.ELIGIBLE,
                behavior_clause(),
                Participant.private_reachable.is_(True),
            )
            .group_by(Participant.id)
            .order_by(Participant.user_id)
        )
        rows = (await session.execute(statement)).all()
        return [(participant, int(weight)) for participant, weight in rows if int(weight) > 0]

    async def _create_snapshot(
        self,
        session: object,
        *,
        campaign: Campaign,
        entries: list[tuple[Participant, int]],
        now: datetime,
    ) -> DrawSnapshot:
        current_prize_hash = await self._validated_prize_hash(session, campaign)
        watermark = await session.scalar(
            select(func.max(TicketLedger.created_at)).where(TicketLedger.campaign_id == campaign.id)
        )
        watermark = watermark or now
        payload = {
            "campaign_id": str(campaign.id),
            "config_revision": campaign.config_revision,
            "prize_plan_hash": current_prize_hash,
            "ledger_watermark": watermark.isoformat(),
            "entries": [(str(participant.id), weight) for participant, weight in entries],
        }
        snapshot = DrawSnapshot(
            campaign_id=campaign.id,
            config_revision=campaign.config_revision,
            ledger_watermark=watermark,
            snapshot_hash=canonical_hash(payload),
            prize_plan_hash=current_prize_hash,
            total_weight=sum(weight for _, weight in entries),
        )
        session.add(snapshot)
        await session.flush()
        for participant, weight in entries:
            check_id = await self._latest_draw_check(session, participant.id)
            session.add(
                DrawEntry(
                    snapshot_id=snapshot.id,
                    participant_id=participant.id,
                    user_id=participant.user_id,
                    weight=weight,
                    qualification_observation_id=check_id,
                )
            )
        return snapshot

    async def _validated_prize_hash(self, session: object, campaign: Campaign) -> str:
        prizes = (
            await session.scalars(
                select(Prize)
                .where(Prize.campaign_id == campaign.id)
                .order_by(Prize.position, Prize.id)
            )
        ).all()
        current_hash = prize_plan_hash(prizes)
        if campaign.prize_plan_hash is None:
            raise SettlementBlocked("prize_plan_not_frozen")
        if current_hash != campaign.prize_plan_hash:
            raise SettlementBlocked("prize_plan_changed_after_publish")
        return current_hash

    async def _latest_draw_check(self, session: object, participant_id: UUID) -> UUID | None:
        return await session.scalar(
            select(QualificationCheck.id)
            .where(
                QualificationCheck.participant_id == participant_id,
                QualificationCheck.phase == "draw",
            )
            .order_by(QualificationCheck.observed_at.desc())
            .limit(1)
        )
