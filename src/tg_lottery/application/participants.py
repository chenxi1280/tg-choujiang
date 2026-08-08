from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_outbox
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import CampaignState, QualificationState, TicketSource
from tg_lottery.domain.rules import CampaignRules, canonical_hash
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    InviteLink,
    Participant,
    QualificationCheck,
    TelegramOutbox,
    TicketLedger,
    UserProfile,
)

_REGISTRATION_STATES = frozenset({CampaignState.SCHEDULED, CampaignState.RUNNING})


@dataclass(frozen=True)
class RegistrationRequest:
    campaign_id: UUID
    user_id: int
    username: str | None
    display_name: str
    observed_at: datetime


@dataclass(frozen=True)
class RegistrationAttempt:
    participant_id: UUID
    qualification_check_id: UUID
    required_channel_id: int | None
    required_chat_ids: tuple[int, ...]
    state: QualificationState
    requires_observation: bool


@dataclass(frozen=True)
class QualificationResult:
    participant_id: UUID
    qualification_check_id: UUID
    state: QualificationState
    observed_at: datetime
    observed_status: str | None = None
    error_code: str | None = None


class ParticipantService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def begin_registration(self, request: RegistrationRequest) -> RegistrationAttempt:
        async with self._uow_factory() as uow:
            campaign = await uow.session.scalar(
                select(Campaign).where(Campaign.id == request.campaign_id).with_for_update()
            )
            if not self._accepts_registration(campaign, request.observed_at):
                raise ValueError("campaign is not accepting registrations")
            await self._validate_profile_requirements(uow.session, campaign, request)
            participant = await self._participant(uow.session, request)
            existing = await self._source_qualification_check(uow.session, participant.id)
            if existing is not None:
                return self._registration_attempt(existing, campaign)
            participant.qualification_state = QualificationState.PENDING
            check = self._pending_qualification_check(participant, campaign, request)
            uow.session.add(check)
            await uow.session.flush()
            return self._registration_attempt(check, campaign)

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
            self._apply_check_result(check, result)
            if self._can_apply_registration_result(campaign, result):
                participant.qualification_state = result.state
                if result.state is QualificationState.ELIGIBLE:
                    await self._ensure_registration_ticket(
                        uow.session,
                        campaign=campaign,
                        participant=participant,
                        observed_at=result.observed_at,
                    )
                    await self._ensure_invite_link(uow.session, campaign, participant)
            return participant

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
    ) -> QualificationCheck:
        async with self._uow_factory() as uow:
            participant = await uow.session.scalar(
                select(Participant).where(Participant.id == participant_id).with_for_update()
            )
            if participant is None:
                raise ValueError("participant not found")
            check = QualificationCheck(
                participant_id=participant.id,
                phase="draw",
                state=state,
                observed_status=observed_status,
                error_code=error_code,
                observed_at=observed_at,
                request_hash=canonical_hash(
                    {"participant_id": str(participant.id), "phase": "draw"}
                ),
                source_update_id=current_update_id(),
            )
            participant.qualification_state = state
            uow.session.add(check)
            return check

    async def _participant(self, session: object, request: RegistrationRequest) -> Participant:
        participant = await session.scalar(
            select(Participant).where(
                Participant.campaign_id == request.campaign_id,
                Participant.user_id == request.user_id,
            )
        )
        if participant:
            participant.username = request.username
            participant.display_name = request.display_name
            participant.private_reachable = True
            return participant
        participant = Participant(
            campaign_id=request.campaign_id,
            user_id=request.user_id,
            username=request.username,
            display_name=request.display_name,
            private_reachable=True,
            qualification_state=QualificationState.PENDING,
            registered_revision=1,
            registered_source_update_id=current_update_id(),
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
        self, check: QualificationCheck, campaign: Campaign
    ) -> RegistrationAttempt:
        required_chat_ids = _required_chat_ids(CampaignRules.model_validate(campaign.rules))
        return RegistrationAttempt(
            participant_id=check.participant_id,
            qualification_check_id=check.id,
            required_channel_id=check.channel_id,
            required_chat_ids=required_chat_ids,
            state=check.state,
            requires_observation=check.state is QualificationState.PENDING,
        )

    def _pending_qualification_check(
        self,
        participant: Participant,
        campaign: Campaign,
        request: RegistrationRequest,
    ) -> QualificationCheck:
        rules = CampaignRules.model_validate(campaign.rules)
        required_chat_ids = _required_chat_ids(rules)
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
            raise ValueError("本抽奖要求设置 Telegram 用户名")
        if not rules.allowed_regions:
            return
        profile = await session.get(UserProfile, request.user_id)
        if profile is None or profile.region not in rules.allowed_regions:
            allowed = "、".join(rules.allowed_regions)
            raise ValueError(f"本抽奖仅限地区: {allowed}, 请先使用 /region 设置")

    async def _ensure_invite_link(
        self, session: object, campaign: Campaign, participant: Participant
    ) -> None:
        group = await session.get(Group, campaign.group_id) if campaign else None
        if group is None:
            raise ValueError("registration campaign or group is missing")
        await self._request_invite_link(
            session,
            campaign=campaign,
            group=group,
            participant=participant,
        )

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

    def _apply_check_result(
        self,
        check: QualificationCheck,
        result: QualificationResult,
    ) -> None:
        check.state = result.state
        check.observed_status = result.observed_status
        check.error_code = result.error_code
        check.observed_at = result.observed_at

    def _can_apply_registration_result(
        self, campaign: Campaign, result: QualificationResult
    ) -> bool:
        return campaign.state in _REGISTRATION_STATES and result.observed_at <= campaign.cutoff_at

    def _accepts_registration(self, campaign: Campaign | None, observed_at: datetime) -> bool:
        return bool(
            campaign
            and campaign.state in _REGISTRATION_STATES
            and observed_at <= campaign.cutoff_at
        )

    async def _request_invite_link(
        self,
        session: object,
        *,
        campaign: Campaign,
        group: Group,
        participant: Participant,
    ) -> None:
        existing_link = await session.scalar(
            select(InviteLink.id).where(InviteLink.participant_id == participant.id)
        )
        business_key = f"invite_link:{campaign.id}:{participant.id}"
        existing_outbox = await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
        )
        if existing_link or existing_outbox:
            return
        add_outbox(
            session,
            operation="create_invite_link",
            aggregate_type="participant",
            aggregate_id=str(participant.id),
            recipient_chat_id=group.chat_id,
            business_key=business_key,
            payload={
                "campaign_id": str(campaign.id),
                "participant_id": str(participant.id),
                "expire_at": campaign.cutoff_at.isoformat(),
                "name": f"lot_{participant.id.hex[:20]}",
            },
        )


def _required_chat_ids(rules: CampaignRules) -> tuple[int, ...]:
    values = ([rules.required_channel_id] if rules.required_channel_id else []) + list(
        rules.required_chat_ids
    )
    return tuple(dict.fromkeys(values))
