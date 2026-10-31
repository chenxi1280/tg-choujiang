from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram.creator_bonus_fields import positive_integer
from tg_lottery.telegram.creator_bonus_policy import uses_current_bonus_policy
from tg_lottery.telegram.creator_retention_revision import checked_retention_editor

QUICK_PREFIX = "draft_gate:quick:"


def handles_quick(action: str) -> bool:
    return action == "draft_gate:save_group" or action.startswith(QUICK_PREFIX)


def input_field(data: dict) -> str:
    current = data[editor.EDITOR_KEY]
    if current.get("field"):
        if current["field"] not in editor.MESSAGE_FIELDS | editor.INVITE_FIELDS:
            raise UserFacingError("请先结束群搜索，再设置门槛数字")
        return str(current["field"])
    if not current.get("chat_id") or "copy_targets" in current or current.get("pending_action"):
        raise UserFacingError("请先选择要设置门槛的群，再发送正整数")
    return "daily_messages" if current.get("type") == "message" else "required_valid_invites"


def prepare_save(data: dict, *, field: str, value: str) -> dict:
    current = data[editor.EDITOR_KEY]
    _require_current_group(data, current)
    changed = editor.apply_action(data, f"draft_gate:preset:{field}:{positive_integer(value)}")
    staged = changed[editor.EDITOR_KEY]
    _require_retention(changed)
    saved = data.get("admission") or {}
    groups = dict(saved.get("groups") or {}) if saved.get("type") == staged["type"] else {}
    key, rule = staged["chat_id"], dict(staged["rule"])
    groups[key] = rule
    updated = staged | {
        "groups": {**staged["groups"], key: rule},
        "rule_baseline": dict(rule),
        "field": None,
        "notice": "💾 已保存本群门槛到当前草稿；正式发布前仍可修改。",
    }
    return changed | {
        "admission": {"type": staged["type"], "groups": groups},
        "base_activity_rules": {},
        "chat_message_thresholds": {},
        editor.EDITOR_KEY: updated,
    }


def _require_current_group(data, current):
    if not current.get("chat_id") or "copy_targets" in current or current.get("pending_action"):
        raise UserFacingError("请先返回本群门槛编辑页")
    if not any(
        str(group["id"]) == current["chat_id"] for group in data.get("participation_groups", [])
    ):
        raise UserFacingError("该参与群已移除，请重新选择")


def _require_retention(data):
    retention = checked_retention_editor(data)["retention_hours"]
    if uses_current_bonus_policy(data) and retention != 24:
        raise UserFacingError("新场邀请留存固定24小时，请返回当前门槛设置页")
    if retention != data.get("invite_retention_hours"):
        raise UserFacingError("请先保存共享邀请留存设置，再快捷保存本群门槛")
