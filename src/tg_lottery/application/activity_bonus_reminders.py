"""Read the same daily counters used to award reputation bonuses."""

from zoneinfo import ZoneInfo

from sqlalchemy import select

from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import CampaignParticipationGroup, DailyProgress


async def activity_bonus_progress_text(
    session, campaign, participant, *, event_at, group_label=None, compact=False
):
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
    if group_label:
        names = {key: group_label(key, names.get(key, key)) for key in targets}
    return _bonus_lines(names, targets, counts, compact=compact)


def _bonus_lines(names, targets, counts, *, compact):
    lines = ["🌱 今日加成" if compact else "发言抽奖加成(今日):"]
    lines.extend(
        f"{names.get(key, '群名称待更新')}: "
        + bonus_cycle_text(rule, counts.get(key, 0), compact=compact)
        for key, rule in targets.items()
    )
    return "\n".join(lines)


def bonus_cycle_text(rule, count, *, compact=False):
    cycles, progress = divmod(count, rule.messages_per_cycle)
    if rule.daily_cap_mode == "limited" and cycles >= rule.daily_max_cycles:
        if compact:
            return f"🏁 今日 +{rule.daily_max_cycles * rule.reward_tickets} 次 · 已达上限"
        return f"已完成 {rule.daily_max_cycles} 轮, 今日已达加成上限。"
    label = "下一轮" if cycles else "本轮"
    remaining = rule.messages_per_cycle - progress
    if compact:
        return (
            f"已加 {cycles * rule.reward_tickets} 次 · "
            f"再发 {remaining} 条 +{rule.reward_tickets} 次"
        )
    return (
        f"已完成 {cycles} 轮; {label} {progress}/{rule.messages_per_cycle} 条, "
        f"还差 {remaining} 条有效发言可增加 {rule.reward_tickets} 次。"
    )
