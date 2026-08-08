import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import (
    CampaignState,
    InvitationState,
    QualificationState,
    TicketSource,
)
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    ActivityEvent,
    Campaign,
    DailyProgress,
    Group,
    Invitation,
    InviteeActivityEvent,
    Participant,
    TicketLedger,
)

_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MessageObservation:
    chat_id: int
    message_id: int
    user_id: int
    event_at: datetime
    text: str | None
    is_bot: bool = False
    is_service: bool = False
    is_forwarded: bool = False


class ActivityService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record_message(
        self, observation: MessageObservation
    ) -> ActivityEvent | InviteeActivityEvent | None:
        async with self._uow_factory() as uow:
            campaign = await self._active_campaign(uow.session, observation.chat_id)
            if campaign is None or not (
                campaign.preheat_at <= observation.event_at <= campaign.cutoff_at
            ):
                return None
            participant = await self._eligible_participant(
                uow.session, campaign.id, observation.user_id
            )
            if participant is None:
                return await self._record_invitee_activity(uow.session, campaign, observation)
            existing = await uow.session.scalar(
                select(ActivityEvent).where(
                    ActivityEvent.chat_id == observation.chat_id,
                    ActivityEvent.message_id == observation.message_id,
                )
            )
            if existing:
                return existing
            return await self._record(
                uow.session,
                campaign=campaign,
                participant=participant,
                observation=observation,
            )

    async def _record_invitee_activity(
        self,
        session: object,
        campaign: Campaign,
        observation: MessageObservation,
    ) -> InviteeActivityEvent | None:
        rules = CampaignRules.model_validate(campaign.rules)
        if not rules.invite.require_activity:
            return None
        invitation = await session.scalar(
            select(Invitation).where(
                Invitation.campaign_id == campaign.id,
                Invitation.invitee_user_id == observation.user_id,
                Invitation.state.in_([InvitationState.JOINED_PENDING, InvitationState.CHECKING]),
            )
        )
        if invitation is None:
            return None
        normalized = _normalize(observation.text)
        reason = _rejection_reason(observation, normalized, rules.activity.min_text_length)
        content_hash = sha256(normalized.encode()).hexdigest()
        duplicate = await self._invitee_duplicate(
            session,
            invitation_id=invitation.id,
            content_hash=content_hash,
            event_at=observation.event_at,
            rules=rules,
        )
        event = InviteeActivityEvent(
            invitation_id=invitation.id,
            chat_id=observation.chat_id,
            message_id=observation.message_id,
            event_at=observation.event_at,
            content_hash=content_hash,
            accepted=reason is None and not duplicate,
            source_update_id=current_update_id(),
        )
        session.add(event)
        return event

    async def _record(
        self,
        session: object,
        *,
        campaign: Campaign,
        participant: Participant,
        observation: MessageObservation,
    ) -> ActivityEvent:
        rules = CampaignRules.model_validate(campaign.rules)
        normalized = _normalize(observation.text)
        content_hash = sha256(normalized.encode()).hexdigest()
        local_date = observation.event_at.astimezone(ZoneInfo(campaign.timezone)).date()
        reason = _rejection_reason(observation, normalized, rules.activity.min_text_length)
        if reason is None:
            reason = await self._duplicate_reason(
                session,
                participant_id=participant.id,
                content_hash=content_hash,
                event_at=observation.event_at,
                rules=rules,
            )
        event = ActivityEvent(
            campaign_id=campaign.id,
            participant_id=participant.id,
            chat_id=observation.chat_id,
            message_id=observation.message_id,
            user_id=observation.user_id,
            event_at=observation.event_at,
            local_date=local_date,
            content_hash=content_hash,
            accepted=reason is None,
            rejection_reason=reason,
            source_update_id=current_update_id(),
        )
        session.add(event)
        if reason is None:
            await self._increment_progress(
                session,
                campaign=campaign,
                participant=participant,
                local_date=local_date,
                event_at=observation.event_at,
                rules=rules,
            )
        return event

    async def _increment_progress(
        self,
        session: object,
        *,
        campaign: Campaign,
        participant: Participant,
        local_date: date,
        event_at: datetime,
        rules: CampaignRules,
    ) -> None:
        statement = insert(DailyProgress).values(
            campaign_id=campaign.id,
            participant_id=participant.id,
            local_date=local_date,
            valid_message_count=0,
            tickets_awarded=0,
        )
        await session.execute(statement.on_conflict_do_nothing())
        progress = await session.scalar(
            select(DailyProgress)
            .where(
                DailyProgress.campaign_id == campaign.id,
                DailyProgress.participant_id == participant.id,
                DailyProgress.local_date == local_date,
            )
            .with_for_update()
        )
        if progress is None:
            raise RuntimeError("daily progress upsert failed")
        progress.valid_message_count += 1
        if rules.luck_mode == "off":
            return
        target = min(
            rules.activity.daily_ticket_cap,
            progress.valid_message_count
            // rules.activity.message_threshold
            * rules.activity.reward_tickets,
        )
        await self._award_new_tickets(
            session,
            campaign=campaign,
            participant=participant,
            progress=progress,
            target=target,
            event_at=event_at,
        )

    async def _award_new_tickets(
        self,
        session: object,
        *,
        campaign: Campaign,
        participant: Participant,
        progress: DailyProgress,
        target: int,
        event_at: datetime,
    ) -> None:
        for ordinal in range(progress.tickets_awarded + 1, target + 1):
            session.add(
                TicketLedger(
                    campaign_id=campaign.id,
                    participant_id=participant.id,
                    delta=1,
                    source_type=TicketSource.POPULARITY,
                    source_ref=str(progress.id),
                    business_key=(
                        f"popularity:{campaign.id}:{participant.user_id}:"
                        f"{progress.local_date.isoformat()}:{ordinal}"
                    ),
                    occurred_at=event_at,
                    source_update_id=current_update_id(),
                )
            )
            participant.ticket_total_cache += 1
        progress.tickets_awarded = target

    async def _active_campaign(self, session: object, chat_id: int) -> Campaign | None:
        return await session.scalar(
            select(Campaign)
            .join(Group, Group.id == Campaign.group_id)
            .where(
                Group.chat_id == chat_id,
                Campaign.state.in_([CampaignState.RUNNING, CampaignState.SETTLING]),
            )
            .with_for_update()
        )

    async def _eligible_participant(
        self, session: object, campaign_id: UUID, user_id: int
    ) -> Participant | None:
        return await session.scalar(
            select(Participant).where(
                Participant.campaign_id == campaign_id,
                Participant.user_id == user_id,
                Participant.qualification_state == QualificationState.ELIGIBLE,
            )
        )

    async def _duplicate_reason(
        self,
        session: object,
        *,
        participant_id: UUID,
        content_hash: str,
        event_at: datetime,
        rules: CampaignRules,
    ) -> str | None:
        lower_bound = event_at - timedelta(minutes=rules.activity.duplicate_window_minutes)
        duplicate = await session.scalar(
            select(ActivityEvent.id).where(
                ActivityEvent.participant_id == participant_id,
                ActivityEvent.content_hash == content_hash,
                ActivityEvent.accepted.is_(True),
                ActivityEvent.event_at >= lower_bound,
                ActivityEvent.event_at <= event_at,
            )
        )
        return "duplicate_text_window" if duplicate else None

    async def _invitee_duplicate(
        self,
        session: object,
        *,
        invitation_id: UUID,
        content_hash: str,
        event_at: datetime,
        rules: CampaignRules,
    ) -> bool:
        lower_bound = event_at - timedelta(minutes=rules.activity.duplicate_window_minutes)
        duplicate = await session.scalar(
            select(InviteeActivityEvent.id).where(
                InviteeActivityEvent.invitation_id == invitation_id,
                InviteeActivityEvent.content_hash == content_hash,
                InviteeActivityEvent.accepted.is_(True),
                InviteeActivityEvent.event_at >= lower_bound,
                InviteeActivityEvent.event_at <= event_at,
            )
        )
        return duplicate is not None


def _normalize(text: str | None) -> str:
    return _SPACE_RE.sub(" ", (text or "").strip().casefold())


def _rejection_reason(
    observation: MessageObservation, normalized: str, minimum_length: int
) -> str | None:
    if observation.is_bot:
        return "bot"
    if observation.is_service:
        return "service"
    if observation.is_forwarded:
        return "forwarded"
    if len(normalized) < minimum_length:
        return "too_short"
    if not any(character.isalnum() for character in normalized):
        return "emoji_or_symbols_only"
    return None
