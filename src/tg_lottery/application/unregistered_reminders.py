"""未报名发言提示: 与发言处理共用活动锁和事务, 不产生参与或计奖事实。"""

from datetime import UTC, timedelta

from sqlalchemy import select

from tg_lottery.application.message_rules import rejection_reason
from tg_lottery.application.records import utcnow
from tg_lottery.application.temporary_reminders import add_temporary_reminder
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Participant, TelegramOutbox

REMINDER_COOLDOWN = timedelta(minutes=5)


async def remind_unregistered_speaker(session, campaign, observation) -> None:
    if utcnow() > campaign.cutoff_at:
        return
    rules = CampaignRules.model_validate(campaign.rules)
    if rejection_reason(observation) or not _has_message_task(rules, observation.rule_target):
        return
    registered = await session.scalar(
        select(Participant.id).where(
            Participant.campaign_id == campaign.id, Participant.user_id == observation.user_id
        )
    )
    if registered is not None:
        return
    prefix = f"unregistered_speech:{campaign.id}:{observation.chat_id}:{observation.user_id}:"
    event_at = observation.event_at.astimezone(UTC)
    recent = await session.scalar(
        select(TelegramOutbox.id).where(
            TelegramOutbox.business_key.startswith(prefix, autoescape=True),
            TelegramOutbox.payload["observed_at"].as_string()
            > (event_at - REMINDER_COOLDOWN).isoformat(timespec="microseconds"),
        )
    )
    if recent is not None:
        return
    await add_temporary_reminder(
        session,
        campaign_id=campaign.id,
        recipient_chat_id=observation.chat_id,
        business_key=prefix + str(observation.message_id),
        campaign_ref=campaign.public_ref,
        user_id=observation.user_id,
        observed_at=event_at.isoformat(timespec="microseconds"),
        text=f"「{campaign.name}」: 你尚未报名, 本条发言未计入本人抽奖进度。"
        "请点击「参与抽奖」查看条件并登记参与。",
    )


def _has_message_task(rules: CampaignRules, chat_id: int) -> bool:
    if str(chat_id) in rules.message_gate_rules or str(chat_id) in rules.message_thresholds:
        return True
    if not rules.activity_bonus_enabled:
        return False
    return not rules.group_activity_rules or rules.activity_rule_for(chat_id) is not None
