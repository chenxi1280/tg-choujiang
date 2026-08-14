from typing import Any

from tg_lottery.domain.rules import CampaignRules


def activity_chat_ids(campaign: object, main_chat_id: int) -> tuple[int, ...]:
    presentation = getattr(campaign, "presentation", {}) or {}
    extras = presentation.get("required_chats") or []
    group_ids = [main_chat_id]
    group_ids.extend(
        int(chat["id"])
        for chat in extras
        if isinstance(chat, dict) and chat.get("type") != "channel" and chat.get("id") is not None
    )
    return tuple(dict.fromkeys(group_ids))


def activity_ticket_target(
    counts: dict[str, Any], chat_ids: tuple[int, ...], rules: CampaignRules
) -> int:
    if not rules.activity_bonus_enabled:
        return 0
    completed = min(
        int(counts.get(str(chat_id), 0)) // rules.activity.message_threshold for chat_id in chat_ids
    )
    if rules.activity.settle_once_per_day:
        return rules.activity.reward_tickets if completed else 0
    return min(rules.activity.daily_ticket_cap, completed * rules.activity.reward_tickets)
