from typing import Any

from tg_lottery.domain.rules import CampaignRules


def activity_chat_ids(campaign: object, main_chat_id: int) -> tuple[int, ...]:
    presentation = getattr(campaign, "presentation", {}) or {}
    participation = presentation.get("participation_groups") or []
    extras = presentation.get("required_chats") or []
    if not participation:
        return _legacy_activity_chat_ids(main_chat_id, extras)
    rules = CampaignRules.model_validate(getattr(campaign, "rules", {}))
    if rules.group_activity_rules or rules.participation_rules_version:
        return _configured_activity_ids(rules)
    group_ids = [
        int(chat["id"])
        for chat in participation
        if isinstance(chat, dict) and chat.get("id") is not None
    ]
    group_ids.extend(int(chat["id"]) for chat in extras if _requires_activity(chat, rules))
    return tuple(dict.fromkeys(group_ids))


def _legacy_activity_chat_ids(main_chat_id: int, extras: list[object]) -> tuple[int, ...]:
    group_ids = [main_chat_id]
    group_ids.extend(
        int(chat["id"])
        for chat in extras
        if isinstance(chat, dict) and chat.get("type") != "channel" and chat.get("id") is not None
    )
    return tuple(dict.fromkeys(group_ids))


def _requires_activity(chat: object, rules: CampaignRules) -> bool:
    return bool(
        isinstance(chat, dict)
        and chat.get("type") != "channel"
        and chat.get("id") is not None
        and str(chat["id"]) in rules.message_thresholds
    )


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


def group_activity_ticket_target(
    counts: dict[str, Any], chat_id: int, rules: CampaignRules
) -> int | None:
    rule = rules.activity_rule_for(chat_id)
    if rule is None:
        return None
    cycles = int(counts.get(str(chat_id), 0)) // rule.messages_per_cycle
    if rule.daily_cap_mode == "limited":
        if rule.daily_max_cycles is None:
            raise ValueError("limited group activity rule is incomplete")
        cycles = min(cycles, rule.daily_max_cycles)
    return cycles * rule.reward_tickets


def _configured_activity_ids(rules: CampaignRules) -> tuple[int, ...]:
    enabled = [int(key) for key, rule in rules.group_activity_rules.items() if rule.enabled]
    return tuple(
        dict.fromkeys(
            [*enabled, *map(int, rules.message_gate_rules), *map(int, rules.message_thresholds)]
        )
    )
