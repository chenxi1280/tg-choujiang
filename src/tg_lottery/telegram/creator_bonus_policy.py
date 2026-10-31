"""Current creation policy; frozen campaigns retain their original rule fields."""

from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import GroupActivityRule, GroupInviteRule

DAILY_BONUS_POLICY = "daily-after-start-v1"


def uses_current_bonus_policy(data: dict[str, Any]) -> bool:
    return data.get("participation_rules_version") == "v3.0" and data.get(
        "bonus_policy_version"
    ) in {"unlimited-v1", DAILY_BONUS_POLICY}


def policy_version(data: dict[str, Any]) -> str | None:
    return (
        data.get("bonus_policy_version")
        if data.get("participation_rules_version") == "v3.0"
        else None
    )


def current_bonus_policy(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("participation_rules_version") != "v3.0" or data.get("publish_snapshot"):
        return dict(data)
    if data.get("published_campaign_id"):
        return dict(data)
    updated = data | {
        "bonus_policy_version": DAILY_BONUS_POLICY,
        "activity_bonus_double_count": data.get("activity_bonus_double_count", True),
        "invite_retention_hours": 24,
        "invite_cap_mode": "unlimited",
        "invite_bonus_cap": None,
    }
    for kind in ("invite", "activity"):
        key = f"group_{kind}_rules"
        updated[key] = {
            group: creation_rule(kind, rule) for group, rule in (data.get(key) or {}).items()
        }
    updated.update(_editor_policy(data))
    note = (
        "新场发言获得的抽奖加成票首日不发、次日起整场每天最多1张；"
        "邀请加成票规则保留，历史活动不变。"
    )
    updated["import_notes"] = _policy_notes(data, note)
    return updated


def _editor_policy(data):
    updated = {}
    working = data.get("bonus_working_copy")
    if isinstance(working, dict):
        updated["bonus_working_copy"] = working | {
            "rule": creation_rule(str(working["kind"]), working["rule"]),
            "double_count": working.get(
                "double_count", data.get("activity_bonus_double_count", True)
            ),
        }
    editor = data.get("admission_editor")
    if isinstance(editor, dict):
        updated["admission_editor"] = {
            **{key: value for key, value in editor.items() if key != "retention_conflict"},
            "retention_hours": 24,
            "retention_base": 24,
            "retention_modified": False,
        }
    return updated


def creation_rule(kind: str, rule: dict[str, Any]) -> dict[str, Any]:
    if kind == "invite":
        return GroupInviteRule.model_validate(
            rule | {"cap_mode": "unlimited", "max_bonus_tickets": None}
        ).model_dump(mode="json")
    return GroupActivityRule.model_validate(
        rule | {"daily_cap_mode": "limited", "daily_max_cycles": 1, "reward_tickets": 1}
    ).model_dump(mode="json")


def require_current_bonus_action(data: dict[str, Any], action: str) -> None:
    if not uses_current_bonus_policy(data):
        return
    if "cap" in action or action.endswith(("_limited", "_activity_daily", "_activity_unlimited")):
        raise UserFacingError("新场抽奖加成票首日不发、每日固定1张；邀请加成票不设上限")
    if "reward" in action and (data.get("bonus_working_copy") or {}).get("kind") == "activity":
        raise UserFacingError("新场抽奖加成票每日固定1张，请使用当前加成票设置页")


def cap_keys(kind: str) -> tuple[str, str]:
    if kind == "invite":
        return "cap_mode", "max_bonus_tickets"
    return "daily_cap_mode", "daily_max_cycles"


def cap_label(rule: dict[str, Any], kind: str) -> str:
    mode_key, cap_key = cap_keys(kind)
    return "不设上限" if rule[mode_key] == "unlimited" else str(rule[cap_key])


def _policy_notes(data, note):
    previous = "新场发言抽奖加成和邀请抽奖加成都不设上限，邀请留存固定24小时；历史活动不变。"
    notes = [value for value in data.get("import_notes") or [] if value != previous]
    return list(dict.fromkeys([*notes, note]))
