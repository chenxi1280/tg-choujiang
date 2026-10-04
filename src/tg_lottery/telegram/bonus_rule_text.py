"""Public descriptions of the same frozen per-group rules used for ticket accounting."""

from collections.abc import Mapping, Sequence

from tg_lottery.domain.rules import CampaignRules, GroupActivityRule, GroupInviteRule
from tg_lottery.telegram.creator_invite_retention import retention_text


def chat_title(chat: Mapping[str, object]) -> str:
    title = str(chat.get("title") or "").strip()
    if title and title != str(chat.get("id")):
        return title
    username = str(chat.get("username") or "").strip().removeprefix("@")
    if username:
        return f"@{username}"
    kind = "频道" if chat.get("type") == "channel" else "群聊"
    return f"{kind}（名称待更新）"


def group_invite_text(rule: GroupInviteRule) -> str:
    cap = "不设上限" if rule.cap_mode == "unlimited" else f"上限 {rule.max_bonus_tickets} 次"
    return f"每邀请 {rule.invites_per_cycle} 个有效新成员 +1 次，{cap}"


def group_activity_text(rule: GroupActivityRule) -> str:
    if rule.daily_cap_mode == "limited" and rule.daily_max_cycles == 1:
        return (
            f"每天发言满 {rule.messages_per_cycle} 条 +{rule.reward_tickets} 次，"
            f"每天只奖励一次，按达标天数累计；每条至少 {rule.min_text_length} 字"
        )
    cap = (
        "每日不设上限"
        if rule.daily_cap_mode == "unlimited"
        else f"每日最多完成 {rule.daily_max_cycles} 轮"
    )
    return (
        f"每日每满 {rule.messages_per_cycle} 条有效消息 +{rule.reward_tickets} 次，"
        f"每条至少 {rule.min_text_length} 字，{cap}"
    )


def per_group_bonus_lines(
    rules: CampaignRules, chats: Sequence[Mapping[str, object]]
) -> tuple[str, ...]:
    by_id = {str(chat["id"]): chat for chat in chats}
    keys = dict.fromkeys((*rules.group_invite_rules, *rules.group_activity_rules))
    lines = []
    for key in keys:
        invite = rules.invite_rule_for(int(key)) if rules.invite_bonus_enabled else None
        activity = rules.activity_rule_for(int(key)) if rules.activity_bonus_enabled else None
        parts = []
        if invite is not None:
            parts.append(group_invite_text(invite))
        if activity is not None:
            parts.append(group_activity_text(activity))
        if parts:
            title = chat_title(by_id.get(key, {}))
            lines.append(f"{title}：" + "；".join(parts))
    lines.extend(base_rule_lines(rules, by_id))
    if rules.invitation_enabled:
        lines.append("有效邀请要求：" + retention_text(rules.invite.retention_hours))
    return tuple(lines)


def base_rule_lines(rules: CampaignRules, by_id: Mapping[str, object]) -> list[str]:
    lines = []
    for key, rule in rules.base_activity_rules.items():
        title = chat_title(by_id.get(key, {}))
        lines.append(
            f"{title}基础门槛：连续 {rule.consecutive_days} 天，"
            f"每天 {rule.daily_messages} 条，每条至少 {rule.min_text_length} 字"
        )
    if rules.base_activity_rules:
        lines.append("北京时间每日统计；先报名再累计；全部基础门槛完成前，加成暂存，不能参与开奖。")
    return lines
