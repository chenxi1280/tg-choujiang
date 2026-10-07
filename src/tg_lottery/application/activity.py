from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from tg_lottery.application.activity_rules import (
    activity_chat_ids,
    activity_ticket_target,
)
from tg_lottery.application.activity_targets import locked_message_campaign, message_rule_chat_id
from tg_lottery.application.admission_qualification import source_qualification_reason
from tg_lottery.application.base_activity import record_base_activity
from tg_lottery.application.group_activity_progress import increment_group_activity_progress
from tg_lottery.application.message_rules import normalized_content, rejection_reason
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.progress_notifications import flush_progress_notices, queue_bonus_notice
from tg_lottery.application.request_context import current_update_id
from tg_lottery.application.unregistered_reminders import remind_unregistered_speaker
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
    CampaignParticipationGroup,
    CampaignRequirement,
    DailyProgress,
    Group,
    Invitation,
    InviteeActivityEvent,
    Participant,
    TicketLedger,
)


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
    rule_chat_id: int | None = None
    media_key: str | None = None

    @property
    def rule_target(self) -> int:
        return self.rule_chat_id if self.rule_chat_id is not None else self.chat_id


class ActivityService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record_message(
        self, observation: MessageObservation
    ) -> ActivityEvent | InviteeActivityEvent | None:
        async with self._uow_factory() as uow:
            campaigns = await self._active_campaigns(uow.session, observation.chat_id)
            recorded = []
            for campaign, main_chat_id in campaigns:
                event = await self._record_for_campaign(
                    uow.session,
                    campaign=campaign,
                    main_chat_id=main_chat_id,
                    observation=observation,
                )
                if event is not None:
                    recorded.append(event)
            return recorded[0] if recorded else None

    async def _record_for_campaign(
        self,
        session: object,
        *,
        campaign: Campaign,
        main_chat_id: int,
        observation: MessageObservation,
    ) -> ActivityEvent | InviteeActivityEvent | None:
        campaign = await locked_message_campaign(session, campaign.id)
        if campaign.state not in {CampaignState.RUNNING, CampaignState.SETTLING}:
            return None
        if not campaign.preheat_at <= observation.event_at <= campaign.cutoff_at:
            return None
        rules = CampaignRules.model_validate(campaign.rules)
        observation = replace(
            observation,
            rule_chat_id=await message_rule_chat_id(session, campaign.id, observation.chat_id),
        )
        participant = await self._eligible_participant(
            session, campaign.id, observation.user_id, include_registered=bool(rules.admission)
        )
        if participant is None:
            return await self._record_unregistered_activity(session, campaign, observation)
        await record_base_activity(
            session, campaign=campaign, participant=participant, observation=observation
        )
        existing = await session.scalar(
            select(ActivityEvent).where(
                ActivityEvent.campaign_id == campaign.id,
                ActivityEvent.chat_id == observation.chat_id,
                ActivityEvent.message_id == observation.message_id,
            )
        )
        if existing:
            return existing
        event = await self._record(
            session,
            campaign=campaign,
            participant=participant,
            observation=observation,
            activity_chat_ids=activity_chat_ids(campaign, main_chat_id),
        )
        await flush_progress_notices(
            session,
            campaign=campaign,
            participant=participant,
            source_ref=f"message:{observation.chat_id}:{observation.message_id}",
            event_at=observation.event_at,
        )
        return event

    async def _record_unregistered_activity(
        self,
        session: object,
        campaign: Campaign,
        observation: MessageObservation,
    ) -> InviteeActivityEvent | None:
        await remind_unregistered_speaker(session, campaign, observation)
        rules = CampaignRules.model_validate(campaign.rules)
        if not rules.invite.require_activity:
            return None
        invitation = await session.scalar(
            select(Invitation)
            .outerjoin(
                CampaignParticipationGroup,
                CampaignParticipationGroup.id == Invitation.participation_group_id,
            )
            .where(
                Invitation.campaign_id == campaign.id,
                Invitation.invitee_user_id == observation.user_id,
                Invitation.state.in_([InvitationState.JOINED_PENDING, InvitationState.CHECKING]),
                or_(
                    Invitation.participation_group_id.is_(None),
                    CampaignParticipationGroup.chat_id_snapshot == observation.chat_id,
                ),
            )
        )
        if invitation is None:
            return None
        existing = await session.scalar(
            select(InviteeActivityEvent).where(
                InviteeActivityEvent.invitation_id == invitation.id,
                InviteeActivityEvent.chat_id == observation.chat_id,
                InviteeActivityEvent.message_id == observation.message_id,
            )
        )
        if existing:
            return existing
        return await self._new_invitee_activity_event(
            session,
            invitation=invitation,
            observation=observation,
            rules=rules,
        )

    async def _new_invitee_activity_event(
        self,
        session: object,
        *,
        invitation: Invitation,
        observation: MessageObservation,
        rules: CampaignRules,
    ) -> InviteeActivityEvent:
        normalized = normalized_content(observation)
        reason = rejection_reason(observation)
        content_hash = sha256(normalized.encode()).hexdigest()
        duplicate = await self._invitee_duplicate(
            session,
            invitation_id=invitation.id,
            chat_id=observation.chat_id,
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
        activity_chat_ids: tuple[int, ...],
    ) -> ActivityEvent:
        rules = CampaignRules.model_validate(campaign.rules)
        normalized = normalized_content(observation)
        content_hash = sha256(normalized.encode()).hexdigest()
        local_date = observation.event_at.astimezone(ZoneInfo(campaign.timezone)).date()
        reason = rejection_reason(observation)
        if rules.admission is not None and reason is None:
            reason = await source_qualification_reason(session, participant, observation)
        if reason is None:
            reason = await self._duplicate_reason(
                session,
                participant_id=participant.id,
                chat_id=observation.chat_id,
                content_hash=content_hash,
                event_at=observation.event_at,
                rules=rules,
            )
        event = _activity_event(
            campaign=campaign,
            participant=participant,
            observation=observation,
            local_date=local_date,
            content_hash=content_hash,
            reason=reason,
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
                chat_id=observation.rule_target,
                activity_chat_ids=activity_chat_ids,
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
        chat_id: int,
        activity_chat_ids: tuple[int, ...],
    ) -> None:
        if not rules.activity_bonus_enabled:
            return
        if rules.activity_rule_for(chat_id) is not None:
            await increment_group_activity_progress(
                session,
                campaign=campaign,
                participant=participant,
                local_date=local_date,
                event_at=event_at,
                rules=rules,
                chat_id=chat_id,
            )
            return
        if rules.group_activity_rules:
            return
        progress = await self._locked_legacy_progress(
            session,
            campaign=campaign,
            participant=participant,
            local_date=local_date,
        )
        counts = dict(progress.valid_message_counts or {})
        counts[str(chat_id)] = int(counts.get(str(chat_id), 0)) + 1
        progress.valid_message_counts = counts
        progress.valid_message_count = sum(int(value) for value in counts.values())
        target = activity_ticket_target(counts, activity_chat_ids, rules)
        await self._award_new_tickets(
            session,
            campaign=campaign,
            participant=participant,
            progress=progress,
            target=target,
            event_at=event_at,
        )

    async def _locked_legacy_progress(
        self,
        session: object,
        *,
        campaign: Campaign,
        participant: Participant,
        local_date: date,
    ) -> DailyProgress:
        statement = insert(DailyProgress).values(
            campaign_id=campaign.id,
            participant_id=participant.id,
            local_date=local_date,
            valid_message_count=0,
            valid_message_counts={},
            tickets_awarded=0,
        )
        await session.execute(statement.on_conflict_do_nothing())
        progress = await session.scalar(
            select(DailyProgress)
            .where(
                DailyProgress.campaign_id == campaign.id,
                DailyProgress.participant_id == participant.id,
                DailyProgress.local_date == local_date,
                DailyProgress.participation_group_id.is_(None),
            )
            .with_for_update()
        )
        if progress is None:
            raise RuntimeError("daily progress upsert failed")
        return progress

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
        delta = target - progress.tickets_awarded
        if delta <= 0:
            return
        session.add(
            TicketLedger(
                campaign_id=campaign.id,
                participant_id=participant.id,
                delta=delta,
                source_type=TicketSource.POPULARITY,
                source_ref=str(progress.id),
                business_key=(
                    f"popularity:{campaign.id}:{participant.user_id}:"
                    f"{progress.local_date.isoformat()}:{target}"
                ),
                occurred_at=event_at,
                source_update_id=current_update_id(),
            )
        )
        participant.ticket_total_cache += delta
        progress.tickets_awarded = target
        await queue_bonus_notice(
            session,
            campaign=campaign,
            participant=participant,
            chat_id=None,
            key=f"popularity:{campaign.id}:{participant.user_id}:{progress.local_date.isoformat()}:{target}",
            label="发言加成",
            delta=delta,
        )

    async def _active_campaigns(
        self, session: object, chat_id: int
    ) -> tuple[tuple[Campaign, int], ...]:
        participation_campaigns = (
            select(CampaignParticipationGroup.campaign_id)
            .join(Group, Group.id == CampaignParticipationGroup.group_id)
            .where(
                or_(
                    Group.chat_id == chat_id,
                    CampaignParticipationGroup.chat_id_snapshot == chat_id,
                )
            )
        )
        requirement_campaigns = (
            select(CampaignRequirement.campaign_id)
            .outerjoin(Group, Group.id == CampaignRequirement.target_group_id)
            .where(
                or_(
                    CampaignRequirement.chat_id_snapshot == chat_id,
                    Group.chat_id == chat_id,
                )
            )
        )
        rows = await session.execute(
            select(Campaign, Group.chat_id)
            .join(Group, Group.id == Campaign.group_id)
            .where(
                Campaign.state.in_([CampaignState.RUNNING, CampaignState.SETTLING]),
                or_(
                    Group.chat_id == chat_id,
                    Campaign.rules.op("@>")({"required_chat_ids": [chat_id]}),
                    Campaign.id.in_(participation_campaigns),
                    Campaign.id.in_(requirement_campaigns),
                ),
            )
            .order_by(Campaign.id)
        )
        targets = []
        for campaign, main_chat_id in rows.all():
            rule_chat_id = await message_rule_chat_id(session, campaign.id, chat_id)
            if rule_chat_id in activity_chat_ids(campaign, main_chat_id):
                targets.append((campaign, main_chat_id))
        return tuple(targets)

    async def _eligible_participant(
        self, session: object, campaign_id: UUID, user_id: int, *, include_registered: bool = False
    ) -> Participant | None:
        return await session.scalar(
            select(Participant)
            .where(
                Participant.campaign_id == campaign_id,
                Participant.user_id == user_id,
                True
                if include_registered
                else Participant.qualification_state == QualificationState.ELIGIBLE,
            )
            .with_for_update()
        )

    async def _duplicate_reason(
        self,
        session: object,
        *,
        participant_id: UUID,
        chat_id: int,
        content_hash: str,
        event_at: datetime,
        rules: CampaignRules,
    ) -> str | None:
        lower_bound = event_at - timedelta(minutes=rules.activity.duplicate_window_minutes)
        duplicate = await session.scalar(
            select(ActivityEvent.id).where(
                ActivityEvent.participant_id == participant_id,
                ActivityEvent.chat_id == chat_id,
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
        chat_id: int,
        content_hash: str,
        event_at: datetime,
        rules: CampaignRules,
    ) -> bool:
        lower_bound = event_at - timedelta(minutes=rules.activity.duplicate_window_minutes)
        duplicate = await session.scalar(
            select(InviteeActivityEvent.id).where(
                InviteeActivityEvent.invitation_id == invitation_id,
                InviteeActivityEvent.chat_id == chat_id,
                InviteeActivityEvent.content_hash == content_hash,
                InviteeActivityEvent.accepted.is_(True),
                InviteeActivityEvent.event_at >= lower_bound,
                InviteeActivityEvent.event_at <= event_at,
            )
        )
        return duplicate is not None


def _activity_event(*, campaign, participant, observation, local_date, content_hash, reason):
    return ActivityEvent(
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
