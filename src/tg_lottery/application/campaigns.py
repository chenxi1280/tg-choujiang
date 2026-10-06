from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.campaign_notifications import (
    add_campaign_card_state_fanout,
    add_cancelled_campaign_card_fanout,
)
from tg_lottery.application.campaign_targets import (
    MULTI_GROUP_CONTRACT,
    add_campaign_targets,
    participation_chat_ids,
    release_participation_groups,
    require_campaign_admin,
    resolve_draft_groups,
)
from tg_lottery.application.draw_commitments import ensure_draw_commitment
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.application.publication import add_publication_outboxes
from tg_lottery.application.publication_recovery import retry_failed_publication
from tg_lottery.application.qualification_recovery import (
    retry_qualification_review,
)
from tg_lottery.application.records import add_audit, add_job, utcnow
from tg_lottery.domain.action_confirmation import ExpectedCampaign, validate_expected
from tg_lottery.domain.admission import validate_admission_window
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
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


@dataclass(frozen=True)
class DraftSource:
    draft_id: str
    revision: int


async def request_early_cutoff(
    session: object,
    *,
    campaign: Campaign,
    cutoff_at: datetime,
    reason: str,
    actor_user_id: int | None,
) -> bool:
    if campaign.state is CampaignState.SETTLING:
        return False
    require_campaign_transition(campaign.state, CampaignState.SETTLING)
    rules = CampaignRules.model_validate(campaign.rules)
    campaign.cutoff_at = cutoff_at
    campaign.draw_at = cutoff_at + timedelta(
        seconds=rules.settlement_drain_seconds + _EARLY_DRAW_DELAY_SECONDS
    )
    campaign.state = CampaignState.SETTLING
    await ensure_draw_commitment(session, campaign, cutoff_at)
    _schedule_early_cutoff_jobs(session, campaign=campaign, cutoff_at=cutoff_at, rules=rules)
    add_audit(
        session,
        actor_user_id=actor_user_id,
        action="campaign.early_cutoff",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        reason=reason,
        details={"cutoff_at": cutoff_at.isoformat()},
    )
    return True


class CampaignService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def create_draft(
        self,
        draft: CampaignDraft,
        prizes: tuple[PrizeSpec, ...],
        actor_user_id: int,
        *,
        source: DraftSource | None = None,
    ) -> Campaign:
        prizes = self._validated_prizes(prizes)
        async with self._uow_factory() as uow:
            groups = await resolve_draft_groups(uow.session, draft, actor_user_id)
            group = groups[0]
            existing = await self._campaign_for_source(uow.session, source)
            if existing is not None:
                self._validate_source_retry(
                    existing, group=group, actor=actor_user_id, source=source
                )
                return existing
            campaign = self._new_campaign(draft, group, actor_user_id, source=source)
            uow.session.add(campaign)
            await uow.session.flush()
            await add_campaign_targets(uow.session, campaign=campaign, draft=draft, groups=groups)
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
            await require_campaign_admin(
                uow.session, campaign_id=campaign.id, user_id=actor_user_id
            )
            if await self._publication_already_requested(uow.session, campaign):
                return
            self._validate_publish_timeline(campaign, utcnow())
            require_campaign_transition(campaign.state, CampaignState.PUBLISHING)
            campaign.state = CampaignState.PUBLISHING
            await self._freeze_revision(uow.session, campaign)
            chat_ids = await participation_chat_ids(uow.session, campaign.id)
            prizes = await self._prizes(uow.session, campaign.id)
            campaign.prize_plan_hash = prize_plan_hash(prizes)
            self._schedule_lifecycle(uow.session, campaign)
            await add_publication_outboxes(
                uow.session,
                campaign=campaign,
                recipient_chat_ids=chat_ids,
                bot_username=bot_username,
                prizes=prizes,
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
        expected: ExpectedCampaign | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            campaign = await self._locked_campaign(uow.session, campaign_id)
            validate_expected(campaign, expected)
            await require_campaign_admin(
                uow.session, campaign_id=campaign.id, user_id=actor_user_id
            )
            if campaign.state is target:
                return
            require_campaign_transition(campaign.state, target)
            campaign.state = target
            await release_participation_groups(uow.session, campaign, utcnow())
            if target is CampaignState.SETTLING:
                await ensure_draw_commitment(uow.session, campaign, utcnow())
                self._add_settlement_job(uow.session, campaign)
            if target is CampaignState.CANCELLED:
                self._add_revoke_job(uow.session, campaign)
                await add_cancelled_campaign_card_fanout(uow.session, campaign)
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action=f"campaign.{target.value}",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
                reason=reason,
            )

    async def retry_failed_publication(
        self, campaign_id: UUID, actor_user_id: int, *, expected: ExpectedCampaign | None = None
    ) -> int:
        return await retry_failed_publication(
            self._uow_factory, campaign_id, actor_user_id, expected=expected
        )

    async def retry_qualification_review(self, campaign_id: UUID, actor_user_id: int) -> None:
        await retry_qualification_review(self._uow_factory, campaign_id, actor_user_id)

    async def early_cutoff(
        self,
        campaign_id: UUID,
        *,
        actor_user_id: int,
        cutoff_at: datetime,
        reason: str,
        expected: ExpectedCampaign | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            campaign = await self._locked_campaign(uow.session, campaign_id)
            validate_expected(campaign, expected)
            await require_campaign_admin(
                uow.session, campaign_id=campaign.id, user_id=actor_user_id
            )
            changed = await request_early_cutoff(
                uow.session,
                campaign=campaign,
                cutoff_at=cutoff_at,
                actor_user_id=actor_user_id,
                reason=reason,
            )
            if not changed:
                return
            await add_campaign_card_state_fanout(uow.session, campaign, state="settling")

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

        try:
            validate_admission_window(
                CampaignRules.model_validate(campaign.rules),
                start_at=now,
                cutoff_at=campaign.cutoff_at,
            )
        except ValueError as exc:
            raise UserFacingError(str(exc)) from exc

    def _new_campaign(
        self,
        draft: CampaignDraft,
        group: Group,
        actor: int,
        *,
        source: DraftSource | None,
    ) -> Campaign:
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
            source_draft_id=source.draft_id if source else None,
            source_draft_revision=source.revision if source else None,
            contract_version=MULTI_GROUP_CONTRACT,
        )

    async def _campaign_for_source(
        self, session: object, source: DraftSource | None
    ) -> Campaign | None:
        if source is None:
            return None
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(source.draft_id, 0)))
        )
        return await session.scalar(
            select(Campaign).where(Campaign.source_draft_id == source.draft_id).with_for_update()
        )

    def _validate_source_retry(
        self,
        campaign: Campaign,
        *,
        group: Group,
        actor: int,
        source: DraftSource | None,
    ) -> None:
        if source is None:
            raise ValueError("draft publication source is missing")
        if (
            campaign.group_id != group.id
            or campaign.created_by != actor
            or campaign.source_draft_revision != source.revision
        ):
            raise ValueError("draft publication source conflicts with an existing campaign")

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
        outbox_id = await session.scalar(
            select(TelegramOutbox.id).where(
                TelegramOutbox.aggregate_type == "campaign",
                TelegramOutbox.aggregate_id == str(campaign.id),
                TelegramOutbox.operation.in_(("send_campaign_card", "create_required_chat_invite")),
            )
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
            payload={"revoke_personal": True},
        )


def _schedule_early_cutoff_jobs(
    session: object,
    *,
    campaign: Campaign,
    cutoff_at: datetime,
    rules: CampaignRules,
) -> None:
    suffix = f"{campaign.id}:{campaign.config_revision}"
    for job_type in ("qualification_review", "revoke_invite_links"):
        add_job(
            session,
            job_type=job_type,
            aggregate_id=str(campaign.id),
            business_key=f"{job_type}_early:{suffix}",
            run_at=cutoff_at,
        )
    add_job(
        session,
        job_type="campaign_settle",
        aggregate_id=str(campaign.id),
        business_key=f"campaign_settle_early:{suffix}",
        run_at=cutoff_at + timedelta(seconds=rules.settlement_drain_seconds),
    )
    add_job(
        session,
        job_type="campaign_draw",
        aggregate_id=str(campaign.id),
        business_key=f"campaign_draw_early:{suffix}",
        run_at=cutoff_at
        + timedelta(seconds=rules.settlement_drain_seconds + _EARLY_DRAW_DELAY_SECONDS),
    )
