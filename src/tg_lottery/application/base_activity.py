from datetime import timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from tg_lottery.application.base_activity_notifications import notify_streak_restart
from tg_lottery.application.full_draw import record_full_draw_candidate
from tg_lottery.application.message_rules import normalize_text, rejection_reason
from tg_lottery.application.progress_notifications import queue_progress_notice
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.base_activity import base_progress
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Participant
from tg_lottery.infrastructure.db.models_base_activity import BaseActivityEvent


async def record_base_activity(
    session: object, *, campaign: object, participant: object, observation: object
) -> None:
    rules = CampaignRules.model_validate(campaign.rules)
    rule = rules.base_activity_rules.get(str(observation.chat_id))
    if rule is None:
        return
    existing = await session.scalar(
        select(BaseActivityEvent.id).where(
            BaseActivityEvent.campaign_id == campaign.id,
            BaseActivityEvent.chat_id == observation.chat_id,
            BaseActivityEvent.message_id == observation.message_id,
        )
    )
    if existing is not None:
        return
    event = await _event(session, participant, observation=observation, rules=rules, rule=rule)
    session.add(event)
    await session.flush([event])
    if not event.accepted:
        return
    progress = await load_base_progress(session, campaign, participant, today=event.local_date)
    was_qualified = participant.base_qualified
    participant.base_qualified = all(item.qualified for item in progress.values())
    await _daily_notice(
        session,
        campaign=campaign,
        participant=participant,
        event=event,
        rule=rule,
        progress=progress,
    )
    if participant.base_qualified and not was_qualified:
        await queue_progress_notice(
            session,
            campaign=campaign,
            participant=participant,
            chat_id=observation.chat_id,
            key=f"base_complete:{participant.id}",
            text="全部基础发言门槛已达标, 可以参与抽奖; 已暂存的加成现在生效。",
        )
        await _trigger_full_draw(session, campaign, observed_at=observation.event_at)


async def _event(
    session: object, participant: object, *, observation: object, rules: CampaignRules, rule: object
) -> BaseActivityEvent:
    normalized = normalize_text(observation.text)
    content_hash = sha256(normalized.encode()).hexdigest()
    reason = rejection_reason(observation, normalized, rule.min_text_length)
    if reason is None:
        duplicate = await session.scalar(
            select(BaseActivityEvent.id).where(
                BaseActivityEvent.participant_id == participant.id,
                BaseActivityEvent.chat_id == observation.chat_id,
                BaseActivityEvent.content_hash == content_hash,
                BaseActivityEvent.accepted.is_(True),
                BaseActivityEvent.event_at
                >= observation.event_at
                - timedelta(minutes=rules.activity.duplicate_window_minutes),
                BaseActivityEvent.event_at <= observation.event_at,
            )
        )
        reason = "duplicate_text_window" if duplicate else None
    return BaseActivityEvent(
        campaign_id=participant.campaign_id,
        participant_id=participant.id,
        chat_id=observation.chat_id,
        message_id=observation.message_id,
        event_at=observation.event_at,
        local_date=observation.event_at.astimezone(ZoneInfo("Asia/Shanghai")).date(),
        content_hash=content_hash,
        accepted=reason is None,
        rejection_reason=reason,
        source_update_id=current_update_id(),
    )


async def load_base_progress(
    session: object, campaign: object, participant: object, *, today: object
):
    rules = CampaignRules.model_validate(campaign.rules)
    rows = await session.execute(
        select(
            BaseActivityEvent.chat_id,
            BaseActivityEvent.local_date,
            func.count(BaseActivityEvent.id),
        )
        .where(
            BaseActivityEvent.participant_id == participant.id,
            BaseActivityEvent.accepted.is_(True),
            BaseActivityEvent.event_at <= campaign.cutoff_at,
        )
        .group_by(BaseActivityEvent.chat_id, BaseActivityEvent.local_date)
    )
    counts = {}
    for chat_id, day, count in rows:
        counts.setdefault(str(chat_id), {})[day] = count
    return {
        key: base_progress(counts.get(key, {}), rule, today)
        for key, rule in rules.base_activity_rules.items()
    }


async def _trigger_full_draw(session: object, campaign: object, *, observed_at: object) -> None:
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.draw_mode != "full":
        return
    await session.flush()
    count = await session.scalar(
        select(func.count(Participant.id)).where(
            Participant.campaign_id == campaign.id,
            Participant.base_qualified.is_(True),
            Participant.qualification_state == QualificationState.ELIGIBLE,
        )
    )
    if count >= rules.participant_target:
        latest = await session.scalar(
            select(func.max(BaseActivityEvent.event_at)).where(
                BaseActivityEvent.campaign_id == campaign.id, BaseActivityEvent.accepted.is_(True)
            )
        )
        observed_at = max(observed_at, latest)
        await record_full_draw_candidate(
            session, campaign=campaign, observed_at=observed_at, eligible_count=count
        )


async def _daily_notice(
    session: object,
    *,
    campaign: object,
    participant: object,
    event: object,
    rule: object,
    progress: object,
) -> None:
    current = progress[str(event.chat_id)]
    if current.today_messages == 1 and not current.qualified:
        await notify_streak_restart(
            session, campaign=campaign, participant=participant, event=event, rule=rule
        )
    if current.today_messages == rule.daily_messages:
        text = (
            f"{event.local_date} 基础发言任务完成: "
            f"{current.today_messages}/{rule.daily_messages} 条; "
            f"连续进度 {min(current.consecutive_days, rule.consecutive_days)}"
            f"/{rule.consecutive_days} 天。"
        )
        if current.qualified:
            text += "本群基础门槛已达标。"
        await queue_progress_notice(
            session,
            campaign=campaign,
            participant=participant,
            chat_id=event.chat_id,
            key=f"base_daily:{participant.id}:{event.chat_id}:{event.local_date}",
            text=text,
        )
