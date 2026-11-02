"""Announcement summaries use the same frozen-rule clauses as complete rules."""

from tg_lottery.presentation.daily_bonus import daily_bonus_summary_text
from tg_lottery.telegram.bonus_rule_text import (
    chat_title,
    global_activity_text,
    global_invite_text,
    group_activity_text,
    group_invite_text,
)
from tg_lottery.telegram.creator_invite_retention import retention_text


def bonus_summary_lines(rules, chats, *, compact=False):
    names = {str(chat["id"]): chat_title(chat) for chat in chats}
    activity = _activity_summary(rules, names, compact=compact)
    invite = _invite_summary(rules, names, compact=compact)
    return (f"🌱 发言加成票：{activity}", f"🤝 邀请加成票：{invite}")


def _activity_summary(rules, names, *, compact):
    if not rules.activity_bonus_enabled:
        return "未开启"
    if rules.daily_activity_bonus:
        return daily_bonus_summary_text() + "各群要求及双算方式见完整规则。"
    if not rules.group_activity_rules:
        return global_activity_text(rules.activity)
    if compact:
        return "各奖励群分别累计，发言条数、奖励和每日上限见完整规则。"
    return "；".join(
        f"{names.get(key, '参与群')}：{group_activity_text(rule)}"
        for key, rule in rules.group_activity_rules.items()
        if rule.enabled
    )


def _invite_summary(rules, names, *, compact):
    if not rules.invite_bonus_enabled:
        return "未开启"
    if not rules.group_invite_rules:
        summary = global_invite_text(rules.invite)
    elif compact:
        summary = "各奖励群分别累计，邀请比例和累计上限见完整规则"
    else:
        summary = "；".join(
            f"{names.get(key, '参与群')}：{group_invite_text(rule)}"
            for key, rule in rules.group_invite_rules.items()
            if rule.enabled
        )
    return summary + "；" + retention_text(rules.invite.retention_hours)
