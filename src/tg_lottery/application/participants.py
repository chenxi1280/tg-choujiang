from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.admission_qualification import behavior_clause
from tg_lottery.application.campaign_sharing import CampaignShareRequest, request_campaign_share
from tg_lottery.application.campaign_targets import QualificationTarget, qualification_targets
from tg_lottery.application.full_draw import record_full_draw_candidate
from tg_lottery.application.group_invite_links import (
    GroupInviteLinkRequest,
    request_group_invite_link,
)
from tg_lottery.application.operational_blocks import (
    has_active_campaign_block,
    project_qualification_blocks,
)
from tg_lottery.application.participant_qualification import (
    accepts_registration,
    apply_check_result,
    build_draw_check,
    can_apply_registration_result,
    mark_membership_ineligible,
    pending_approval_chat_ids,
    source_participation_group_id,
    store_target_observations,
    target_chat_ids,
)
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_job
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import QualificationState, TicketSource
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.public_refs import new_invite_link_name, new_referral_public_ref
from tg_lottery.domain.rules import CampaignRules, canonical_hash
from tg_lottery.infrastructure.db import (
    Campaign,
    Participant,
    QualificationCheck,
    ScheduledJob,
    TicketLedger,
    UserProfile,
)


@dataclass(frozen=True)
class RegistrationRequest:
    campaign_id: UUID
    user_id: int
    username: str | None
    display_name: str
    observed_at: datetime
    source_chat_id: int | None = None


@dataclass(frozen=True)
class RegistrationAttempt:
    participant_id: UUID
    qualification_check_id: UUID
    required_channel_id: int | None
    required_chat_ids: tuple[int, ...]
    state: QualificationState
    requires_observation: bool
    target_kinds: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class QualificationResult:
    participant_id: UUID
    qualification_check_id: UUID
    state: QualificationState
    observed_at: datetime
    observed_status: str | None = None
    error_code: str | None = None
    observations: tuple[dict[str, Any], ...] = ()


class ParticipantService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        invite_name_factory: Callable[[], str] = new_invite_link_name,
        referral_ref_factory: Callable[[], str] = new_referral_public_ref,
    ) -> None:
        self._uow_factory = uow_factory
        self._invite_name_factory = invite_name_factory
        self._referral_ref_factory = referral_ref_factory

    async def request_share_card(
        self, campaign_id: UUID, user_id: int, *, bot_username: str
    ) -> None:
        await request_campaign_share(
            self._uow_factory, CampaignShareRequest(campaign_id, user_id, bot_username)
        )

    async def begin_registration(self, request: RegistrationRequest) -> RegistrationAttempt:
        async with self._uow_factory() as uow:
            campaign = await uow.session.scalar(
                select(Campaign).where(Campaign.id == request.campaign_id).with_for_update()
            )
            if not accepts_registration(campaign, request.observed_at):
                raise UserFacingError("活动当前不接受报名")
            participant = await self._existing_participant(uow.session, request)
            active_block = await has_active_campaign_block(uow.session, campaign.id)
            if active_block and participant is None:
                raise UserFacingError("活动正在等待 Telegram 权限恢复, 暂时不能报名")
            await self._validate_profile_requirements(uow.session, campaign, request)
            targets = await qualification_targets(uow.session, campaign)
            participant = participant or await self._new_participant(
                uow.session, request, campaign=campaign
            )
            existing = await self._source_qualification_check(uow.session, participant.id)
            if existing is not None:
                return self._registration_attempt(existing, campaign, targets)
            participant.qualification_state = QualificationState.PENDING
            check = self._pending_qualification_check(
                participant, campaign, request, targets=targets
            )
            uow.session.add(check)
            await uow.session.flush()
            return self._registration_attempt(check, campaign, targets)

    async def eligible_participant(self, campaign_id: UUID, user_id: int) -> Participant | None:
        async with self._uow_factory() as uow:
            participant = await uow.session.scalar(
                select(Participant).where(
                    Participant.campaign_id == campaign_id,
                    Participant.user_id == user_id,
                    Participant.qualification_state == QualificationState.ELIGIBLE,
                )
            )
            if participant is not None:
                uow.session.expunge(participant)
            return participant

    async def request_group_invite_link(
        self, campaign_id: UUID, user_id: int, chat_id: int, *, bot_username: str
    ) -> None:
        await request_group_invite_link(
            self._uow_factory,
            request=GroupInviteLinkRequest(campaign_id, user_id, chat_id, bot_username),
            name_factory=self._invite_name_factory,
            ref_factory=self._referral_ref_factory,
        )

    async def mark_membership_ineligible(
        self,
        *,
        campaign_id: UUID,
        user_id: int,
        chat_id: int,
        observed_at: datetime,
        observed_status: str = "left",
    ) -> None:
        await mark_membership_ineligible(
            self._uow_factory,
            campaign_id=campaign_id,
            user_id=user_id,
            chat_id=chat_id,
            observed_at=observed_at,
            observed_status=observed_status,
        )

    async def pending_approval_chat_ids(self, participant_id: UUID) -> frozenset[int]:
        return await pending_approval_chat_ids(self._uow_factory, participant_id)

    async def complete_registration(self, result: QualificationResult) -> Participant:
        async with self._uow_factory() as uow:
            campaign_id = await self._registration_campaign_id(uow.session, result)
            campaign = await uow.session.scalar(
                select(Campaign).where(Campaign.id == campaign_id).with_for_update()
            )
            if campaign is None:
                raise ValueError("registration campaign is missing")
            check = await uow.session.get(
                QualificationCheck, result.qualification_check_id, with_for_update=True
            )
            if (
                check is None
                or check.participant_id != result.participant_id
                or check.phase != "registration"
            ):
                raise ValueError("registration qualification check is missing")
            participant = await uow.session.get(
                Participant, result.participant_id, with_for_update=True
            )
            if participant is None:
                raise ValueError("registration participant is missing")
            if check.state is not QualificationState.PENDING:
                return participant
            apply_check_result(check, result)
            store_target_observations(uow.session, check, result)
            await project_qualification_blocks(
                uow.session,
                campaign_id=campaign.id,
                observations=result.observations,
            )
            if not can_apply_registration_result(campaign, result.observed_at):
                return participant
            participant.qualification_state = result.state
            if result.state is QualificationState.ELIGIBLE:
                await self._complete_eligible_registration(
                    uow.session, campaign=campaign, participant=participant, result=result
                )
            return participant

    async def _complete_eligible_registration(
        self,
        session: object,
        *,
        campaign: Campaign,
        participant: Participant,
        result: QualificationResult,
    ) -> None:
        if participant.counting_started_at is None:
            participant.counting_started_at = result.observed_at
            participant.counting_started_update_id = current_update_id()
        await self._ensure_registration_ticket(
            session,
            campaign=campaign,
            participant=participant,
            observed_at=result.observed_at,
        )
        await self._trigger_full_draw_if_ready(
            session, campaign=campaign, observed_at=result.observed_at
        )

    async def schedule_message_deletion(
        self,
        *,
        campaign_id: UUID,
        chat_id: int,
        message_id: int,
        run_at: datetime,
    ) -> None:
        business_key = f"delete_message:{campaign_id}:{chat_id}:{message_id}"
        async with self._uow_factory() as uow:
            existing = await uow.session.scalar(
                select(ScheduledJob.id).where(ScheduledJob.business_key == business_key)
            )
            if existing:
                return
            add_job(
                uow.session,
                job_type="delete_telegram_message",
                aggregate_id=str(campaign_id),
                business_key=business_key,
                run_at=run_at,
                payload={"chat_id": chat_id, "message_id": message_id},
            )

    async def _trigger_full_draw_if_ready(
        self, session: object, *, campaign: Campaign, observed_at: datetime
    ) -> None:
        rules = CampaignRules.model_validate(campaign.rules)
        if rules.draw_mode != "full" or rules.participant_target is None:
            return
        await session.flush()
        count = await session.scalar(
            select(func.count(Participant.id)).where(
                behavior_clause(),
                Participant.campaign_id == campaign.id,
                Participant.qualification_state == QualificationState.ELIGIBLE,
            )
        )
        if int(count or 0) < rules.participant_target:
            return
        await record_full_draw_candidate(
            session,
            campaign=campaign,
            observed_at=observed_at,
            eligible_count=int(count or 0),
        )

    async def _registration_campaign_id(self, session: object, result: QualificationResult) -> UUID:
        row = (
            await session.execute(
                select(QualificationCheck.participant_id, Participant.campaign_id)
                .join(Participant, Participant.id == QualificationCheck.participant_id)
                .where(QualificationCheck.id == result.qualification_check_id)
            )
        ).one_or_none()
        if row is None or row.participant_id != result.participant_id:
            raise ValueError("registration qualification check is missing")
        return row.campaign_id

    async def record_draw_qualification(
        self,
        *,
        participant_id: UUID,
        state: QualificationState,
        observed_at: datetime,
        observed_status: str | None,
        error_code: str | None,
        observations: tuple[dict[str, Any], ...] = (),
    ) -> QualificationCheck:
        async with self._uow_factory() as uow:
            participant = await uow.session.scalar(
                select(Participant).where(Participant.id == participant_id).with_for_update()
            )
            if participant is None:
                raise ValueError("participant not found")
            check = build_draw_check(
                participant,
                state=state,
                observed_at=observed_at,
                observed_status=observed_status,
                error_code=error_code,
                observations=observations,
            )
            participant.qualification_state = state
            uow.session.add(check)
            await uow.session.flush()
            store_target_observations(
                uow.session,
                check,
                QualificationResult(
                    participant_id=participant.id,
                    qualification_check_id=check.id,
                    state=state,
                    observed_at=observed_at,
                    observed_status=observed_status,
                    error_code=error_code,
                    observations=observations,
                ),
            )
            await project_qualification_blocks(
                uow.session,
                campaign_id=participant.campaign_id,
                observations=observations,
            )
            return check

    async def _existing_participant(
        self, session: object, request: RegistrationRequest
    ) -> Participant | None:
        participant = await session.scalar(
            select(Participant).where(
                Participant.campaign_id == request.campaign_id,
                Participant.user_id == request.user_id,
            )
        )
        if participant is None:
            return None
        participant.username = request.username
        participant.display_name = request.display_name
        participant.private_reachable = True
        return participant

    async def _new_participant(
        self, session: object, request: RegistrationRequest, *, campaign: Campaign
    ) -> Participant:
        participant = Participant(
            campaign_id=request.campaign_id,
            user_id=request.user_id,
            username=request.username,
            display_name=request.display_name,
            private_reachable=True,
            qualification_state=QualificationState.PENDING,
            base_qualified=not bool(campaign.rules.get("base_activity_rules")),
            admission_qualified=campaign.rules.get("participation_rules_version") != "v3.0",
            registration_intent_at=request.observed_at,
            registered_revision=1,
            registered_source_update_id=current_update_id(),
            registered_via_participation_group_id=await source_participation_group_id(
                session,
                campaign_id=request.campaign_id,
                source_chat_id=request.source_chat_id,
            ),
        )
        session.add(participant)
        await session.flush()
        return participant

    async def _source_qualification_check(
        self, session: object, participant_id: UUID
    ) -> QualificationCheck | None:
        source_update_id = current_update_id()
        if source_update_id is None:
            return None
        return await session.scalar(
            select(QualificationCheck).where(
                QualificationCheck.participant_id == participant_id,
                QualificationCheck.phase == "registration",
                QualificationCheck.source_update_id == source_update_id,
            )
        )

    def _registration_attempt(
        self,
        check: QualificationCheck,
        campaign: Campaign,
        targets: tuple[QualificationTarget, ...],
    ) -> RegistrationAttempt:
        required_chat_ids = target_chat_ids(campaign, targets)
        return RegistrationAttempt(
            participant_id=check.participant_id,
            qualification_check_id=check.id,
            required_channel_id=check.channel_id,
            required_chat_ids=required_chat_ids,
            state=check.state,
            requires_observation=check.state is QualificationState.PENDING,
            target_kinds=tuple((target.chat_id, target.kind) for target in targets),
        )

    def _pending_qualification_check(
        self,
        participant: Participant,
        campaign: Campaign,
        request: RegistrationRequest,
        *,
        targets: tuple[QualificationTarget, ...],
    ) -> QualificationCheck:
        required_chat_ids = target_chat_ids(campaign, targets)
        return QualificationCheck(
            participant_id=participant.id,
            phase="registration",
            state=QualificationState.PENDING,
            channel_id=required_chat_ids[0] if required_chat_ids else None,
            observed_status="checking",
            observed_at=request.observed_at,
            request_hash=canonical_hash(
                {
                    "campaign_id": str(campaign.id),
                    "user_id": request.user_id,
                    "observed_at": request.observed_at.isoformat(),
                    "required_chat_ids": required_chat_ids,
                }
            ),
            source_update_id=current_update_id(),
        )

    async def _validate_profile_requirements(
        self, session: object, campaign: Campaign, request: RegistrationRequest
    ) -> None:
        rules = CampaignRules.model_validate(campaign.rules)
        if rules.username_required and not request.username:
            raise UserFacingError("本抽奖要求设置 Telegram 用户名")
        if not rules.allowed_regions:
            return
        profile = await session.get(UserProfile, request.user_id)
        if profile is None or profile.region not in rules.allowed_regions:
            allowed = "、".join(rules.allowed_regions)
            raise UserFacingError(f"本抽奖仅限地区: {allowed}, 请先使用 /region 设置")

    async def _ensure_registration_ticket(
        self,
        session: object,
        *,
        campaign: Campaign,
        participant: Participant,
        observed_at: datetime,
    ) -> None:
        business_key = f"registration:{campaign.id}:{participant.id}"
        existing = await session.scalar(
            select(TicketLedger.id).where(TicketLedger.business_key == business_key)
        )
        if existing is not None:
            return
        session.add(
            TicketLedger(
                campaign_id=campaign.id,
                participant_id=participant.id,
                delta=1,
                source_type=TicketSource.REGISTRATION,
                source_ref=str(participant.id),
                business_key=business_key,
                occurred_at=observed_at,
                source_update_id=current_update_id(),
            )
        )
        participant.ticket_total_cache += 1
