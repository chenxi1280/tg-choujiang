from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_outbox
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    InviteLink,
    Participant,
    QualificationCheck,
    TelegramOutbox,
)

_REGISTRATION_STATES = frozenset({CampaignState.SCHEDULED, CampaignState.RUNNING})


@dataclass(frozen=True)
class RegistrationRequest:
    campaign_id: UUID
    user_id: int
    username: str | None
    display_name: str
    observed_at: datetime
    qualification_state: QualificationState
    observed_channel_status: str | None = None
    error_code: str | None = None


class ParticipantService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def register(self, request: RegistrationRequest) -> Participant:
        async with self._uow_factory() as uow:
            campaign = await uow.session.scalar(
                select(Campaign).where(Campaign.id == request.campaign_id).with_for_update()
            )
            if campaign is None or campaign.state not in _REGISTRATION_STATES:
                raise ValueError("campaign is not accepting registrations")
            participant = await self._participant(uow.session, request)
            check = self._qualification_check(participant, campaign, request)
            uow.session.add(check)
            participant.qualification_state = request.qualification_state
            await uow.session.flush()
            if request.qualification_state is QualificationState.ELIGIBLE:
                group = await uow.session.get(Group, campaign.group_id)
                if group is None:
                    raise ValueError("campaign group is missing")
                await self._request_invite_link(
                    uow.session,
                    campaign=campaign,
                    group=group,
                    participant=participant,
                )
            return participant

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
            qualification_state=request.qualification_state,
            registered_revision=1,
        )
        session.add(participant)
        await session.flush()
        return participant

    def _qualification_check(
        self,
        participant: Participant,
        campaign: Campaign,
        request: RegistrationRequest,
    ) -> QualificationCheck:
        return QualificationCheck(
            participant_id=participant.id,
            phase="registration",
            state=request.qualification_state,
            channel_id=campaign.rules.get("required_channel_id"),
            observed_status=request.observed_channel_status,
            error_code=request.error_code,
            observed_at=request.observed_at,
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
