from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.admission_qualification import behavior_qualified
from tg_lottery.application.base_activity_views import base_progress_text
from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts, load_campaign_outcome
from tg_lottery.application.operational_issue_queries import (
    OperationalIssue,
    load_operational_issues,
)
from tg_lottery.application.participant_results import (
    ParticipantDrawResult,
    load_participant_draw_result,
)
from tg_lottery.application.participation_progress import saved_membership_progress
from tg_lottery.domain.enums import CampaignState, QualificationState, TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Campaign, Participant, TicketLedger


@dataclass(frozen=True)
class ParticipantCampaignDetail:
    public_ref: str
    campaign_name: str
    state: CampaignState
    qualification: QualificationState
    total: int
    registration: int
    activity: int
    invitation: int
    bonus_enabled: bool
    invite_enabled: bool
    draw_at: datetime
    timezone: str
    membership_progress: str = ""
    base_progress: str = ""
    base_qualified: bool = True
    invite_gate_enabled: bool = False
    draw_result: ParticipantDrawResult | None = None
    outcome: CampaignOutcomeFacts | None = None
    cutoff_at: datetime | None = None
    admission_type: str | None = None
    invite_join_deadline: datetime | None = None
    operational_issues: tuple[OperationalIssue, ...] = ()


async def load_participant_campaign_detail(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, public_ref: str
) -> ParticipantCampaignDetail | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Campaign, Participant)
                .join(Participant, Participant.campaign_id == Campaign.id)
                .where(Campaign.public_ref == public_ref, Participant.user_id == user_id)
            )
        ).one_or_none()
        if row is None:
            return None
        campaign, participant = row
        totals = await _ticket_totals(session, participant.id)
        rules = CampaignRules.model_validate(campaign.rules)
        outcome = await load_campaign_outcome(session, campaign.id)
        return ParticipantCampaignDetail(
            draw_result=await load_participant_draw_result(
                session, campaign, participant.id, outcome=outcome
            ),
            outcome=outcome,
            base_progress=await base_progress_text(session, campaign, participant),
            membership_progress=await saved_membership_progress(session, campaign, participant),
            base_qualified=behavior_qualified(participant, campaign),
            public_ref=campaign.public_ref,
            campaign_name=campaign.name,
            state=campaign.state,
            qualification=participant.qualification_state,
            total=sum(totals.values()),
            registration=totals.get(TicketSource.REGISTRATION, 0),
            activity=totals.get(TicketSource.POPULARITY, 0),
            invitation=totals.get(TicketSource.INVITATION, 0)
            + totals.get(TicketSource.REVERSAL, 0),
            bonus_enabled=bool(rules.bonus_types),
            invite_enabled=rules.invitation_enabled,
            invite_gate_enabled=rules.invite_admission_enabled,
            draw_at=campaign.draw_at,
            timezone=campaign.timezone,
            cutoff_at=campaign.cutoff_at,
            admission_type=rules.admission.type if rules.admission else None,
            invite_join_deadline=campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours),
            operational_issues=await load_operational_issues(session, campaign.id),
        )


async def _ticket_totals(session: AsyncSession, participant_id: UUID) -> dict[TicketSource, int]:
    rows = await session.execute(
        select(TicketLedger.source_type, func.sum(TicketLedger.delta))
        .where(TicketLedger.participant_id == participant_id)
        .group_by(TicketLedger.source_type)
    )
    return {source: int(total) for source, total in rows}
