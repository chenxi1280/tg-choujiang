from typing import Any


def reusable_v3_draft(data: dict[str, Any]) -> dict[str, Any]:
    notes = _import_notes(data)
    invite_rules = data.get("group_invite_rules") or {}
    return data | {
        "participation_rules_version": "v3.0",
        "admission": data.get("admission"),
        "base_activity_rules": {},
        "chat_message_thresholds": {},
        "invite_cap_mode": "unlimited",
        "invite_reward_mode": "ratio",
        "invite_bonus_cap": None,
        "group_invite_rules": {
            key: rule | {"cap_mode": "unlimited", "max_bonus_tickets": None}
            for key, rule in invite_rules.items()
        },
        "import_notes": list(dict.fromkeys(notes)),
    }


def _import_notes(data: dict[str, Any]) -> list[str]:
    notes = list(data.get("import_notes") or [])
    gate = data.get("admission")
    if not gate:
        old = data.get("base_activity_rules") or data.get("chat_message_thresholds") or {}
        notes.append("旧配置没有新门槛类型，请明确选择发言数或邀请好友。")
        if old:
            notes.append("原发言条件: " + str(old))
    if _has_limited_invites(data):
        notes.append("本场邀请加成改为不限累计上限；原活动规则保持不变。")
    if data.get("invite_reward_mode") == "tiered":
        ratio = data["valid_invites_per_ticket"]
        notes.append(
            f"旧阶梯邀请奖励不支持无限累计；本场改为每 {ratio} 个有效邀请加 1 次，请核对。"
        )
    return notes


def _has_limited_invites(data: dict) -> bool:
    return data.get("invite_cap_mode") == "limited" or any(
        rule.get("cap_mode") == "limited"
        for rule in (data.get("group_invite_rules") or {}).values()
    )
