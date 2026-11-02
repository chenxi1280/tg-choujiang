"""One prerequisite reminder per saved qualification check; never count speech."""

from sqlalchemy import select

from tg_lottery.application.admission_qualification import (
    source_qualification_reason,
    uses_counting_start,
)
from tg_lottery.application.participation_progress import (
    brief_task_progress,
    latest_membership_check,
    saved_membership_progress,
)
from tg_lottery.application.records import utcnow
from tg_lottery.application.temporary_reminders import add_temporary_reminder
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import TelegramOutbox
from tg_lottery.presentation.counting_notice import blocked_counting_notice


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
    progress = brief_task_progress(await saved_membership_progress(session, campaign, participant))
    await add_temporary_reminder(
        session,
        campaign_id=campaign.id,
        recipient_chat_id=observation.chat_id,
        business_key=key,
        campaign_ref=campaign.public_ref,
        user_id=participant.user_id,
        observed_at=observation.event_at.isoformat(),
        text=blocked_counting_notice(campaign, registered=bool(participant.counting_started_at))
        + "\n"
        + progress
        + "\n请打开本场详情补齐条件并重新检查。",
    )


async def countable_speech_reason(session, *, campaign, participant, observation):
    if not uses_counting_start(CampaignRules.model_validate(campaign.rules)):
        return None
    reason = await source_qualification_reason(session, participant, observation)
    await remind_blocked_speaker(
        session, campaign=campaign, participant=participant, observation=observation, reason=reason
    )
    return reason
