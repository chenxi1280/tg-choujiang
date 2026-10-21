"""Read the same daily counters used to award reputation bonuses."""

from zoneinfo import ZoneInfo

from sqlalchemy import select

from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import CampaignParticipationGroup, DailyProgress


async def activity_bonus_progress_text(session, campaign, participant, *, event_at):
    rules = CampaignRules.model_validate(campaign.rules)
    if not rules.activity_bonus_enabled or event_at >= campaign.cutoff_at:
        return ""
    targets = {
        key: rule
        for key in rules.group_activity_rules
        if (rule := rules.activity_rule_for(int(key)))
    }
    if not targets:
        return ""
    # Sessions disable autoflush; include the message that triggered this notice.
    await session.flush()
    rows = await session.execute(
        select(CampaignParticipationGroup.chat_id_snapshot, DailyProgress.valid_message_count)
        .join(DailyProgress, DailyProgress.participation_group_id == CampaignParticipationGroup.id)
        .where(
            DailyProgress.campaign_id == campaign.id,
            DailyProgress.participant_id == participant.id,
            DailyProgress.local_date == event_at.astimezone(ZoneInfo(campaign.timezone)).date(),
        )
    )
    counts = {str(chat_id): count for chat_id, count in rows}
    names = {
        str(item["id"]): item["title"]
        for item in campaign.presentation.get("participation_groups", [])
    }
    lines = ["人品加成(今日):"]
    lines.extend(
        f"{names.get(key, key)}: " + bonus_cycle_text(rule, counts.get(key, 0))
        for key, rule in targets.items()
    )
    return "\n".join(lines)


def bonus_cycle_text(rule, count):
    cycles, progress = divmod(count, rule.messages_per_cycle)
    if rule.daily_cap_mode == "limited" and cycles >= rule.daily_max_cycles:
        return f"已完成 {rule.daily_max_cycles} 轮, 今日已达加成上限。"
    label = "下一轮" if cycles else "本轮"
    remaining = rule.messages_per_cycle - progress
    return (
        f"已完成 {cycles} 轮; {label} {progress}/{rule.messages_per_cycle} 条, "
        f"还差 {remaining} 条有效发言可增加 {rule.reward_tickets} 次。"
    )
