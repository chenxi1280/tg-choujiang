from datetime import timedelta

from sqlalchemy import func, select

from tg_lottery.application.admission_qualification import behavior_clause
from tg_lottery.application.progress_notifications import (
    flush_progress_notices,
    queue_progress_notice,
)
from tg_lottery.infrastructure.db import BaseActivityEvent, Participant


async def notify_unqualified_at_cutoff(session: object, campaign: object) -> None:
    rules = campaign.rules.get("base_activity_rules") or {}
    gate = campaign.rules.get("admission") or {}
    rules = gate.get("groups") or rules
    if not rules:
        return
    participants = await session.scalars(
        select(Participant).where(Participant.campaign_id == campaign.id, ~behavior_clause())
    )
    for participant in participants:
        await queue_progress_notice(
            session,
            campaign=campaign,
            participant=participant,
            chat_id=int(next(iter(rules))),
            key=f"base_cutoff:{participant.id}",
            text=(
                "本期报名已截止: 行为门槛未完成, 本期不能参与开奖。已累计加成保留记录, 本期不生效。"
            ),
        )
        await flush_progress_notices(
            session,
            campaign=campaign,
            participant=participant,
            source_ref="cutoff",
            event_at=campaign.cutoff_at,
        )


async def notify_streak_restart(
    session: object,
    *,
    campaign: object,
    participant: object,
    event: object,
    rule: object,
) -> None:
    prior = list(
        await session.scalars(
            select(BaseActivityEvent.local_date)
            .where(
                BaseActivityEvent.participant_id == participant.id,
                BaseActivityEvent.chat_id == event.chat_id,
                BaseActivityEvent.accepted.is_(True),
                BaseActivityEvent.local_date < event.local_date,
            )
            .group_by(BaseActivityEvent.local_date)
            .having(func.count(BaseActivityEvent.id) >= rule.daily_messages)
            .order_by(BaseActivityEvent.local_date)
        )
    )
    if not prior or prior[-1] >= event.local_date - timedelta(days=1):
        return
    await queue_progress_notice(
        session,
        campaign=campaign,
        participant=participant,
        chat_id=event.chat_id,
        key=f"base_restart:{participant.id}:{event.chat_id}:{event.local_date}",
        text="此前连续发言已中断, 今天重新累计连续天数。已获得的加成仍暂存, 不会因断签被清空。",
    )
