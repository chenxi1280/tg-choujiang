from datetime import datetime

from sqlalchemy import select

from tg_lottery.application.full_draw import record_full_draw_candidate
from tg_lottery.application.progress_notifications import queue_progress_notice
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import AdmissionProgress, CampaignParticipationGroup, Participant


async def record_group_admission(
    session: object,
    *,
    campaign: object,
    participant: object,
    chat_id: int,
    event_at: datetime,
    source_ref: str,
) -> bool:
    rules = CampaignRules.model_validate(campaign.rules)
    target = await session.scalar(
        select(CampaignParticipationGroup).where(
            CampaignParticipationGroup.campaign_id == campaign.id,
            CampaignParticipationGroup.chat_id_snapshot == chat_id,
        )
    )
    if target is None or rules.admission is None:
        raise ValueError("admission target is missing")
    existing = await session.scalar(
        select(AdmissionProgress).where(
            AdmissionProgress.participant_id == participant.id,
            AdmissionProgress.participation_group_id == target.id,
            AdmissionProgress.mode == rules.admission.type,
        )
    )
    if existing is not None and existing.qualified:
        return False
    _save_admission(
        session,
        existing,
        campaign=campaign,
        participant=participant,
        target=target,
        rules=rules,
        event_at=event_at,
        source_ref=source_ref,
    )
    await session.flush()
    await _complete_admission(
        session,
        campaign=campaign,
        participant=participant,
        rules=rules,
        chat_id=chat_id,
        event_at=event_at,
    )
    return True


def _save_admission(
    session, existing, *, campaign, participant, target, rules, event_at, source_ref
):
    if existing is None:
        existing = AdmissionProgress(
            campaign_id=campaign.id,
            participant_id=participant.id,
            participation_group_id=target.id,
            mode=rules.admission.type,
        )
        session.add(existing)
    existing.qualified = True
    existing.config_revision = campaign.config_revision
    existing.completed_at = event_at
    existing.source_ref = source_ref
    existing.source_update_id = current_update_id()


async def _complete_admission(
    session: object,
    *,
    campaign: object,
    participant: object,
    rules: CampaignRules,
    chat_id: int,
    event_at: datetime,
) -> None:
    completed = set(
        await session.scalars(
            select(CampaignParticipationGroup.chat_id_snapshot)
            .join(
                AdmissionProgress,
                AdmissionProgress.participation_group_id == CampaignParticipationGroup.id,
            )
            .where(
                AdmissionProgress.participant_id == participant.id,
                AdmissionProgress.mode == rules.admission.type,
                AdmissionProgress.qualified.is_(True),
            )
        )
    )
    was_qualified = participant.admission_qualified
    participant.admission_qualified = set(map(int, rules.admission.groups)).issubset(completed)
    if participant.admission_qualified and not was_qualified:
        await queue_progress_notice(
            session,
            campaign=campaign,
            participant=participant,
            chat_id=chat_id,
            key=f"admission_complete:{participant.id}",
            text="本场全部行为门槛已完成; 成员等其他条件合格时, 已暂存加成生效。",
        )
        await trigger_admission_full_draw(session, campaign, observed_at=event_at)


async def trigger_admission_full_draw(session: object, campaign: object, *, observed_at: datetime):
    from sqlalchemy import func

    from tg_lottery.application.admission_qualification import behavior_clause
    from tg_lottery.domain.enums import QualificationState

    rules = CampaignRules.model_validate(campaign.rules)
    if rules.draw_mode != "full" or rules.participant_target is None:
        return
    await session.flush()
    count = int(
        await session.scalar(
            select(func.count(Participant.id)).where(
                Participant.campaign_id == campaign.id,
                behavior_clause(),
                Participant.qualification_state == QualificationState.ELIGIBLE,
            )
        )
        or 0
    )
    if count >= rules.participant_target:
        await record_full_draw_candidate(
            session, campaign=campaign, observed_at=observed_at, eligible_count=count
        )
