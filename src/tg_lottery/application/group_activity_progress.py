from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from tg_lottery.application.activity_rules import group_activity_ticket_target
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    DailyProgress,
    Participant,
    TicketLedger,
)


async def increment_group_activity_progress(
    session: object,
    *,
    campaign: Campaign,
    participant: Participant,
    local_date: date,
    event_at: datetime,
    rules: CampaignRules,
    chat_id: int,
) -> None:
    group_id = await _participation_group_id(session, campaign.id, chat_id)
    progress = await _locked_progress(
        session,
        campaign=campaign,
        participant=participant,
        local_date=local_date,
        group_id=group_id,
    )
    progress.valid_message_count += 1
    progress.valid_message_counts = {str(chat_id): progress.valid_message_count}
    target = group_activity_ticket_target(progress.valid_message_counts, chat_id, rules)
    if target is None:
        raise RuntimeError("group activity rule disappeared during progress update")
    await _award_target(
        session,
        campaign=campaign,
        participant=participant,
        progress=progress,
        group_id=group_id,
        chat_id=chat_id,
        target=target,
        event_at=event_at,
    )


async def _locked_progress(
    session: object,
    *,
    campaign: Campaign,
    participant: Participant,
    local_date: date,
    group_id: object,
) -> DailyProgress:
    statement = insert(DailyProgress).values(
        campaign_id=campaign.id,
        participant_id=participant.id,
        participation_group_id=group_id,
        local_date=local_date,
        valid_message_count=0,
        valid_message_counts={},
        tickets_awarded=0,
    )
    await session.execute(
        statement.on_conflict_do_nothing(
            index_elements=(
                DailyProgress.campaign_id,
                DailyProgress.participant_id,
                DailyProgress.local_date,
                DailyProgress.participation_group_id,
            ),
            index_where=DailyProgress.participation_group_id.is_not(None),
        )
    )
    progress = await session.scalar(
        select(DailyProgress)
        .where(
            DailyProgress.campaign_id == campaign.id,
            DailyProgress.participant_id == participant.id,
            DailyProgress.local_date == local_date,
            DailyProgress.participation_group_id == group_id,
        )
        .with_for_update()
    )
    if progress is None:
        raise RuntimeError("group daily progress upsert failed")
    return progress


async def _award_target(
    session: object,
    *,
    campaign: Campaign,
    participant: Participant,
    progress: DailyProgress,
    group_id: object,
    chat_id: int,
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
            participation_group_id=group_id,
            delta=delta,
            source_type=TicketSource.POPULARITY,
            source_ref=str(progress.id),
            business_key=(
                f"group_activity:{campaign.id}:{participant.user_id}:"
                f"{chat_id}:{progress.local_date.isoformat()}:{target}"
            ),
            occurred_at=event_at,
            source_update_id=current_update_id(),
        )
    )
    progress.tickets_awarded = target
    participant.ticket_total_cache += delta


async def _participation_group_id(session: object, campaign_id: object, chat_id: int) -> object:
    group_id = await session.scalar(
        select(CampaignParticipationGroup.id).where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            CampaignParticipationGroup.chat_id_snapshot == chat_id,
        )
    )
    if group_id is None:
        raise RuntimeError("group activity rule has no participation group")
    return group_id
