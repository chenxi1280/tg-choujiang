from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from tg_lottery.application.authorization import require_group_admin
from tg_lottery.application.campaign_notifications import add_cancelled_campaign_card
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.application.records import add_audit, add_job, add_outbox, utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignDraft, CampaignRules, canonical_hash
from tg_lottery.domain.transitions import require_campaign_transition
from tg_lottery.infrastructure.db import Campaign, Group, Prize, RuleRevision, TelegramOutbox

_MAX_PRIZE_NAME_LENGTH = 120
_EARLY_DRAW_DELAY_SECONDS = 1


@dataclass(frozen=True)
class PrizeSpec:
    name: str
    winner_count: int
    claim_instructions: str


class CampaignService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def create_draft(
        self,
        draft: CampaignDraft,
        prizes: tuple[PrizeSpec, ...],
        actor_user_id: int,
    ) -> Campaign:
        prizes = self._validated_prizes(prizes)
        async with self._uow_factory() as uow:
            group = await self._group(uow.session, draft.group_id)
            await require_group_admin(uow.session, group_id=group.id, user_id=actor_user_id)
            campaign = self._new_campaign(draft, group, actor_user_id)
            uow.session.add(campaign)
            await uow.session.flush()
            self._add_rules_and_prizes(
                uow.session,
                campaign=campaign,
                draft=draft,
                prizes=prizes,
                actor=actor_user_id,
            )
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action="campaign.created",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
            )
            return campaign

    async def publish(self, campaign_id: UUID, actor_user_id: int, bot_username: str) -> None:
        async with self._uow_factory() as uow:
            campaign = await self._locked_campaign(uow.session, campaign_id)
            await require_group_admin(
                uow.session, group_id=campaign.group_id, user_id=actor_user_id
            )
            if await self._publication_already_requested(uow.session, campaign):
                return
            self._validate_publish_timeline(campaign, utcnow())
            require_campaign_transition(campaign.state, CampaignState.PUBLISHING)
            campaign.state = CampaignState.PUBLISHING
            revision = await self._freeze_revision(uow.session, campaign)
            group = await self._group_by_id(uow.session, campaign.group_id)
            prizes = await self._prizes(uow.session, campaign.id)
            campaign.prize_plan_hash = prize_plan_hash(prizes)
            self._schedule_lifecycle(uow.session, campaign)
            add_outbox(
                uow.session,
                operation="send_campaign_card",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
                recipient_chat_id=group.chat_id,
                business_key=f"publish:{campaign.id}:{revision.revision}",
                payload=self._campaign_card(campaign, bot_username, prizes),
            )
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action="campaign.publish_requested",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
            )

    async def change_state(
        self,
        campaign_id: UUID,
        *,
        actor_user_id: int,
        target: CampaignState,
        reason: str,
    ) -> None:
        async with self._uow_factory() as uow:
            campaign = await self._locked_campaign(uow.session, campaign_id)
            await require_group_admin(
                uow.session, group_id=campaign.group_id, user_id=actor_user_id
            )
            if campaign.state is target:
                return
            require_campaign_transition(campaign.state, target)
            campaign.state = target
            if target is CampaignState.SETTLING:
                self._add_settlement_job(uow.session, campaign)
            if target is CampaignState.CANCELLED:
                self._add_revoke_job(uow.session, campaign)
                group = await self._group_by_id(uow.session, campaign.group_id)
                add_cancelled_campaign_card(uow.session, campaign, group.chat_id)
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action=f"campaign.{target.value}",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
                reason=reason,
            )

    async def early_cutoff(
        self,
        campaign_id: UUID,
        *,
        actor_user_id: int,
        cutoff_at: datetime,
        reason: str,
    ) -> None:
        async with self._uow_factory() as uow:
            campaign = await self._locked_campaign(uow.session, campaign_id)
            await require_group_admin(
                uow.session, group_id=campaign.group_id, user_id=actor_user_id
            )
            if campaign.state is CampaignState.SETTLING:
                return
            require_campaign_transition(campaign.state, CampaignState.SETTLING)
            rules = CampaignRules.model_validate(campaign.rules)
            campaign.cutoff_at = cutoff_at
            campaign.draw_at = cutoff_at + timedelta(
                seconds=rules.settlement_drain_seconds + _EARLY_DRAW_DELAY_SECONDS
            )
            campaign.state = CampaignState.SETTLING
            self._schedule_early_cutoff_jobs(
                uow.session,
                campaign=campaign,
                cutoff_at=cutoff_at,
                rules=rules,
            )
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action="campaign.early_cutoff",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
                reason=reason,
                details={"cutoff_at": cutoff_at.isoformat()},
            )

    def _validated_prizes(self, prizes: tuple[PrizeSpec, ...]) -> tuple[PrizeSpec, ...]:
        if not prizes:
            raise ValueError("campaign requires at least one prize")
        normalized = tuple(
            PrizeSpec(prize.name.strip(), prize.winner_count, prize.claim_instructions.strip())
            for prize in prizes
        )
        if any(prize.winner_count < 1 or not prize.name for prize in normalized):
            raise ValueError("each prize requires a name and positive winner count")
        if any(len(prize.name) > _MAX_PRIZE_NAME_LENGTH for prize in normalized):
            raise ValueError("prize name exceeds 120 characters")
        if any(not prize.claim_instructions for prize in normalized):
            raise ValueError("each prize requires claim instructions")
        return normalized

    def _validate_publish_timeline(self, campaign: Campaign, now: datetime) -> None:
        if campaign.cutoff_at <= now:
            raise ValueError("campaign cutoff must be in the future when published")
        if campaign.draw_at <= campaign.cutoff_at:
            raise ValueError("campaign draw must be after cutoff")

    async def _group(self, session: object, group_id: int) -> Group:
        group = await session.scalar(select(Group).where(Group.chat_id == group_id))
        if group is None:
            raise ValueError("target Telegram group is not registered")
        return group

    async def _group_by_id(self, session: object, group_id: UUID) -> Group:
        group = await session.get(Group, group_id)
        if group is None:
            raise ValueError("campaign group does not exist")
        return group

    def _new_campaign(self, draft: CampaignDraft, group: Group, actor: int) -> Campaign:
        return Campaign(
            group_id=group.id,
            name=draft.name,
            state=CampaignState.DRAFT,
            timezone=draft.rules.timezone,
            preheat_at=draft.preheat_at,
            cutoff_at=draft.cutoff_at,
            draw_at=draft.draw_at,
            rules=draft.rules.model_dump(mode="json"),
            presentation=draft.presentation,
            created_by=actor,
        )

    def _add_rules_and_prizes(
        self,
        session: object,
        *,
        campaign: Campaign,
        draft: CampaignDraft,
        prizes: tuple[PrizeSpec, ...],
        actor: int,
    ) -> None:
        rules = draft.rules.model_dump(mode="json")
        session.add(
            RuleRevision(
                campaign_id=campaign.id,
                revision=1,
                rules=rules,
                rules_hash=canonical_hash(rules),
                actor_user_id=actor,
                frozen=False,
            )
        )
        for position, spec in enumerate(prizes, start=1):
            session.add(
                Prize(
                    campaign_id=campaign.id,
                    position=position,
                    name=spec.name,
                    winner_count=spec.winner_count,
                    claim_instructions=spec.claim_instructions,
                )
            )

    async def _locked_campaign(self, session: object, campaign_id: UUID) -> Campaign:
        campaign = await session.scalar(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        )
        if campaign is None:
            raise ValueError("campaign not found")
        return campaign

    async def _freeze_revision(self, session: object, campaign: Campaign) -> RuleRevision:
        revision = await session.scalar(
            select(RuleRevision).where(
                RuleRevision.campaign_id == campaign.id,
                RuleRevision.revision == campaign.config_revision,
            )
        )
        if revision is None:
            raise ValueError("campaign rule revision is missing")
        revision.frozen = True
        return revision

    async def _prizes(self, session: object, campaign_id: UUID) -> tuple[Prize, ...]:
        rows = await session.scalars(
            select(Prize).where(Prize.campaign_id == campaign_id).order_by(Prize.position, Prize.id)
        )
        return tuple(rows)

    async def _publication_already_requested(self, session: object, campaign: Campaign) -> bool:
        if campaign.state is CampaignState.DRAFT:
            return False
        business_key = f"publish:{campaign.id}:{campaign.config_revision}"
        outbox_id = await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
        )
        if outbox_id is None:
            raise ValueError("campaign left draft without a publication outbox")
        return True

    def _schedule_lifecycle(self, session: object, campaign: Campaign) -> None:
        rules = CampaignRules.model_validate(campaign.rules)
        add_job(
            session,
            job_type="campaign_start",
            aggregate_id=str(campaign.id),
            business_key=f"campaign_start:{campaign.id}:{campaign.config_revision}",
            run_at=campaign.preheat_at,
        )
        add_job(
            session,
            job_type="campaign_cutoff",
            aggregate_id=str(campaign.id),
            business_key=f"campaign_cutoff:{campaign.id}:{campaign.config_revision}",
            run_at=campaign.cutoff_at,
        )
        add_job(
            session,
            job_type="qualification_review",
            aggregate_id=str(campaign.id),
            business_key=f"qualification_review:{campaign.id}:{campaign.config_revision}",
            run_at=campaign.cutoff_at,
        )
        add_job(
            session,
            job_type="revoke_invite_links",
            aggregate_id=str(campaign.id),
            business_key=f"revoke_links:{campaign.id}:{campaign.config_revision}",
            run_at=campaign.cutoff_at,
        )
        add_job(
            session,
            job_type="campaign_settle",
            aggregate_id=str(campaign.id),
            business_key=f"campaign_settle:{campaign.id}:{campaign.config_revision}",
            run_at=campaign.cutoff_at + timedelta(seconds=rules.settlement_drain_seconds),
        )
        add_job(
            session,
            job_type="campaign_draw",
            aggregate_id=str(campaign.id),
            business_key=f"campaign_draw:{campaign.id}:{campaign.config_revision}",
            run_at=campaign.draw_at,
        )

    def _add_settlement_job(self, session: object, campaign: Campaign) -> None:
        add_job(
            session,
            job_type="campaign_settle",
            aggregate_id=str(campaign.id),
            business_key=f"campaign_settle_manual:{campaign.id}:{campaign.config_revision}",
            run_at=utcnow(),
        )

    def _add_revoke_job(self, session: object, campaign: Campaign) -> None:
        add_job(
            session,
            job_type="revoke_invite_links",
            aggregate_id=str(campaign.id),
            business_key=f"revoke_links_cancel:{campaign.id}:{campaign.config_revision}",
            run_at=utcnow(),
        )

    def _schedule_early_cutoff_jobs(
        self,
        session: object,
        *,
        campaign: Campaign,
        cutoff_at: datetime,
        rules: CampaignRules,
    ) -> None:
        suffix = int(cutoff_at.timestamp())
        for job_type in ("qualification_review", "revoke_invite_links"):
            add_job(
                session,
                job_type=job_type,
                aggregate_id=str(campaign.id),
                business_key=f"{job_type}_early:{campaign.id}:{suffix}",
                run_at=cutoff_at,
            )
        add_job(
            session,
            job_type="campaign_settle",
            aggregate_id=str(campaign.id),
            business_key=f"campaign_settle_early:{campaign.id}:{suffix}",
            run_at=cutoff_at + timedelta(seconds=rules.settlement_drain_seconds),
        )
        add_job(
            session,
            job_type="campaign_draw",
            aggregate_id=str(campaign.id),
            business_key=f"campaign_draw_early:{campaign.id}:{suffix}",
            run_at=cutoff_at
            + timedelta(seconds=rules.settlement_drain_seconds + _EARLY_DRAW_DELAY_SECONDS),
        )

    def _campaign_card(
        self, campaign: Campaign, bot_username: str, prizes: tuple[Prize, ...]
    ) -> dict[str, object]:
        zone = ZoneInfo(campaign.timezone)
        return {
            "campaign_id": str(campaign.id),
            "name": campaign.name,
            "cutoff_at": campaign.cutoff_at.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
            "draw_at": campaign.draw_at.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
            "bot_username": bot_username,
            "presentation": campaign.presentation,
            "rules": campaign.rules,
            "prizes": [
                {
                    "position": prize.position,
                    "name": prize.name,
                    "winner_count": prize.winner_count,
                }
                for prize in prizes
            ],
            "total_winners": sum(prize.winner_count for prize in prizes),
        }
