"""Current creation policy; frozen campaigns retain their original rule fields."""

from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import GroupActivityRule, GroupInviteRule

UNLIMITED_BONUS_POLICY = "unlimited-v1"


def uses_unlimited_policy(data: dict[str, Any]) -> bool:
    return (
        data.get("participation_rules_version") == "v3.0"
        and data.get("bonus_policy_version") == UNLIMITED_BONUS_POLICY
    )


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
        "bonus_policy_version": UNLIMITED_BONUS_POLICY,
        "invite_retention_hours": 24,
        "invite_cap_mode": "unlimited",
        "invite_bonus_cap": None,
    }
    for kind in ("invite", "activity"):
        key = f"group_{kind}_rules"
        updated[key] = {
            group: unlimited_rule(kind, rule) for group, rule in (data.get(key) or {}).items()
        }
    updated.update(_editor_policy(data))
    if updated != data:
        note = "新场活跃和邀请加成都不设上限，邀请留存固定24小时；历史活动不变。"
        updated["import_notes"] = list(dict.fromkeys([*(data.get("import_notes") or []), note]))
    return updated


def _editor_policy(data):
    updated = {}
    working = data.get("bonus_working_copy")
    if isinstance(working, dict):
        updated["bonus_working_copy"] = working | {
            "rule": unlimited_rule(str(working["kind"]), working["rule"])
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


def unlimited_rule(kind: str, rule: dict[str, Any]) -> dict[str, Any]:
    if kind == "invite":
        return GroupInviteRule.model_validate(
            rule | {"cap_mode": "unlimited", "max_bonus_tickets": None}
        ).model_dump(mode="json")
    return GroupActivityRule.model_validate(
        rule | {"daily_cap_mode": "unlimited", "daily_max_cycles": None}
    ).model_dump(mode="json")


def require_current_bonus_action(data: dict[str, Any], action: str) -> None:
    if not uses_unlimited_policy(data):
        return
    if "cap" in action or action.endswith(("_limited", "_activity_daily")):
        raise UserFacingError("新场活跃和邀请加成都不设上限，请使用当前加成设置页")
