"""One prerequisite reminder per saved qualification check; never count speech."""

from sqlalchemy import select

from tg_lottery.application.admission_qualification import (
    source_qualification_reason,
    uses_counting_start,
)
from tg_lottery.application.participation_progress import (
    COUNTING_NOTICE,
    brief_task_progress,
    latest_membership_check,
    saved_membership_progress,
)
from tg_lottery.application.records import utcnow
from tg_lottery.application.temporary_reminders import add_temporary_reminder
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import TelegramOutbox


async def remind_blocked_speaker(session, *, campaign, participant, observation, reason):
    if reason not in {"counting_not_started", "membership_ineligible"}:
        return
    if utcnow() > campaign.cutoff_at:
        return
    check = await latest_membership_check(session, participant.id)
    check_key = str(check.id) if check else "unconfirmed"
    key = f"blocked_speech:{participant.id}:{observation.rule_target}:{check_key}"
    if await session.scalar(select(TelegramOutbox.id).where(TelegramOutbox.business_key == key)):
        return
    status = "已报名" if participant.counting_started_at else "已提交报名请求,尚未成功"
    progress = brief_task_progress(await saved_membership_progress(session, campaign, participant))
    await add_temporary_reminder(
        session,
        campaign_id=campaign.id,
        recipient_chat_id=observation.chat_id,
        business_key=key,
        campaign_ref=campaign.public_ref,
        user_id=participant.user_id,
        observed_at=observation.event_at.isoformat(),
        text=f"「{campaign.name}」:{status},前置条件尚未通过,本条发言不计数。\n"
        + progress
        + "\n"
        + COUNTING_NOTICE
        + "\n请打开本场详情补齐条件并重新核验。",
    )


async def countable_speech_reason(session, *, campaign, participant, observation):
    if not uses_counting_start(CampaignRules.model_validate(campaign.rules)):
        return None
    reason = await source_qualification_reason(session, participant, observation)
    await remind_blocked_speaker(
        session, campaign=campaign, participant=participant, observation=observation, reason=reason
    )
    return reason
