from datetime import timedelta

from sqlalchemy import case, or_, select

from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import Campaign, Participant, QualificationCheck


class SourceQualificationUnknown(RuntimeError):
    """Keep the original Inbox pending until its historical qualification is known."""


def behavior_qualified(participant: object, campaign: object) -> bool:
    if campaign.rules.get("participation_rules_version") == "v3.0":
        return bool(participant.admission_qualified)
    return bool(participant.base_qualified)


def behavior_clause():
    """Version dispatch applies even in queries spanning old and new campaigns."""
    version = (
        select(Campaign.rules["participation_rules_version"].as_string())
        .where(Campaign.id == Participant.campaign_id)
        .correlate(Participant)
        .scalar_subquery()
    )
    return case(
        (version == "v3.0", Participant.admission_qualified), else_=Participant.base_qualified
    ).is_(True)


def counting_start_reason(participant: object, observation: object) -> str | None:
    start = participant.counting_started_at
    if start is None:
        return "counting_not_started"
    event_second = observation.event_at.replace(microsecond=0)
    start_second = start.replace(microsecond=0)
    if event_second < start_second:
        return "before_counting_start"
    source = current_update_id()
    watermark = participant.counting_started_update_id
    if event_second == start_second and source is not None and watermark is not None:
        if source <= watermark:
            return "before_counting_start"
        return None
    return "before_counting_start" if observation.event_at < start else None


async def source_qualification_reason(session: object, participant: object, observation: object):
    reason = counting_start_reason(participant, observation)
    if reason is not None:
        return reason
    source = current_update_id()
    statement = select(QualificationCheck).where(
        QualificationCheck.participant_id == participant.id,
        QualificationCheck.state != QualificationState.PENDING,
        QualificationCheck.observed_at
        < observation.event_at.replace(microsecond=0) + timedelta(seconds=1),
    )
    if source is not None:
        statement = statement.where(
            or_(
                QualificationCheck.source_update_id.is_(None),
                QualificationCheck.source_update_id <= source,
            )
        )
    check = await session.scalar(
        statement.order_by(
            QualificationCheck.observed_at.desc(),
            QualificationCheck.source_update_id.desc().nullslast(),
        ).limit(1)
    )
    if check is None or check.state == QualificationState.UNKNOWN:
        raise SourceQualificationUnknown(
            f"participant {participant.id}: qualification at source update {source} is unknown"
        )
    return None if check.state == QualificationState.ELIGIBLE else "membership_ineligible"


def effective_ticket_text(participant, *, active: bool):
    if participant.qualification_state is QualificationState.UNKNOWN:
        return "待核查"
    return str(participant.ticket_total_cache if active else 0)
