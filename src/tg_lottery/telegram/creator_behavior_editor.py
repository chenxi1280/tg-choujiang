from typing import Any

from tg_lottery.domain.admission import InviteAdmissionRule, MessageAdmissionRule
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_bonus_policy import uses_unlimited_policy
from tg_lottery.telegram.creator_number_controls import DEFAULT_MESSAGE_COUNT
from tg_lottery.telegram.creator_retention_revision import checked_retention_editor
from tg_lottery.telegram.creator_schedule import positive_int

EDITOR_KEY = "admission_editor"
PAGE_SIZE = 6
MESSAGE_FIELDS = {"daily_messages", "consecutive_days"}
INVITE_FIELDS = {"required_valid_invites"}


def open_editor(data: dict[str, Any]) -> dict[str, Any]:
    gate = data.get("admission") or {}
    editor = {
        "type": gate.get("type"),
        "groups": dict(gate.get("groups") or {}),
        "chat_id": None,
        "field": None,
        "page": 0,
        "search": "",
        "retention_hours": data.get("invite_retention_hours"),
        "retention_base": data.get("invite_retention_hours"),
        "retention_modified": False,
    }
    return data | {EDITOR_KEY: editor}


def dirty(data: dict[str, Any]) -> bool:
    editor = data.get(EDITOR_KEY) or {}
    gate = {"type": editor.get("type"), "groups": editor.get("groups") or {}}
    retention_dirty = bool(editor) and editor.get("retention_hours") != data.get(
        "invite_retention_hours"
    )
    return (
        group_dirty(data)
        or retention_dirty
        or (bool(editor) and gate != (data.get("admission") or {"type": None, "groups": {}}))
    )


def group_dirty(data: dict) -> bool:
    editor = data.get(EDITOR_KEY) or {}
    if not editor.get("chat_id"):
        return False
    baseline = editor.get("rule_baseline")
    if baseline is None:
        initial = (
            {"daily_messages": DEFAULT_MESSAGE_COUNT} if editor.get("type") == "message" else {}
        )
        saved = editor["groups"].get(editor["chat_id"]) or initial
        baseline = _rule_model(editor, saved).model_dump()
    return _rule_model(editor, editor.get("rule")).model_dump() != baseline


def clear(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key != EDITOR_KEY}


def apply_action(data: dict[str, Any], action: str) -> dict[str, Any]:
    parts = action.removeprefix("draft_gate:").split(":")
    editor = dict(data.get(EDITOR_KEY) or open_editor(data)[EDITOR_KEY])
    editor.pop("notice", None)
    operation = parts[0]
    if operation in {"confirm_change", "keep_editing", "save_and_continue"}:
        return _confirmation_action(data, editor, operation=operation)
    if _needs_confirmation(data, operation, parts):
        return data | {EDITOR_KEY: editor | {"pending_action": action}}
    if operation == "mode":
        if parts[1] not in {"message", "invite"}:
            raise UserFacingError("门槛只能选择发言数或邀请好友")
        return data | {EDITOR_KEY: _select_mode(editor, parts[1])}
    if operation == "save":
        return _save_admission(data, editor)
    if operation == "discard":
        return clear(data)
    return data | {EDITOR_KEY: _edit_action(data, editor, parts=parts)}


def _needs_confirmation(data, operation, parts):
    editor = data.get(EDITOR_KEY) or {}
    if operation == "mode" and editor.get("type") != parts[1] and editor.get("groups"):
        return True
    return operation in {"list", "mode", "group"} and group_dirty(data)


def _edit_action(data: dict, editor: dict, *, parts: list[str]) -> dict:
    operation = parts[0]
    if operation == "group":
        _group(data, parts[1])
        initial = {"daily_messages": DEFAULT_MESSAGE_COUNT} if editor["type"] == "message" else {}
        saved = editor["groups"].get(parts[1]) or initial
        rule = _rule_model(editor, saved).model_dump()
        return editor | {
            "chat_id": parts[1],
            "rule": rule,
            "rule_baseline": dict(rule),
            "field": None,
        }
    if operation in {"input", "search"}:
        return _input_action(editor, parts=parts)
    if operation == "cancel_input":
        return editor | {"field": None}
    if operation == "list":
        return editor | {"chat_id": None, "field": None}
    if operation == "page":
        return editor | {"page": max(0, int(parts[1]))}
    if operation == "clear_search":
        return editor | {"search": "", "page": 0, "field": None}
    if operation == "preset":
        return _set_value(editor, field=parts[1], text=parts[2])
    return _save_group(data, editor, operation=operation)


def _select_mode(editor: dict, mode: str) -> dict:
    groups = editor["groups"] if editor["type"] == mode else {}
    cleared = {key: value for key, value in editor.items() if key not in {"rule", "copy_targets"}}
    return cleared | {
        "type": mode,
        "groups": groups,
        "chat_id": None,
        "field": None,
        "search": "",
        "page": 0,
    }


def _save_group(data: dict, editor: dict, *, operation: str) -> dict:
    chat_id = str(editor.get("chat_id") or "")
    _group(data, chat_id)
    groups = dict(editor["groups"])
    if operation == "disable":
        groups = {key: rule for key, rule in groups.items() if key != chat_id}
    elif operation == "group_save":
        groups[chat_id] = _rule_model(editor, editor["rule"]).model_dump()
    elif operation.startswith("copy_"):
        return _copy_action(data, editor, operation=operation)
    else:
        raise UserFacingError("门槛操作已失效，请返回重新设置")
    return editor | {"groups": groups, "chat_id": None}


def _copy_action(data: dict, editor: dict, *, operation: str) -> dict:
    if operation == "copy_open":
        return editor | {"copy_targets": []}
    if operation == "copy_cancel":
        return {key: value for key, value in editor.items() if key != "copy_targets"}
    if operation == "copy_confirm":
        if not editor.get("copy_targets"):
            raise UserFacingError("请先选择要复制的目标群")
        for target in editor["copy_targets"]:
            _group(data, target)
        groups = dict(editor["groups"])
        groups.update({key: dict(editor["rule"]) for key in editor.get("copy_targets", [])})
        return {
            key: value
            for key, value in (editor | {"groups": groups}).items()
            if key != "copy_targets"
        }
    raise UserFacingError("复制操作已失效")


def receive(data: dict[str, Any], text: str) -> dict[str, Any]:
    editor = dict(data[EDITOR_KEY])
    if editor.get("field") == "search":
        return data | {EDITOR_KEY: editor | {"search": text.strip(), "page": 0, "field": None}}
    return data | {EDITOR_KEY: _set_value(editor, field=editor.get("field"), text=text)}


def _set_value(editor: dict, *, field: str, text: str) -> dict:
    fields = MESSAGE_FIELDS if editor.get("type") == "message" else INVITE_FIELDS
    if field not in fields:
        raise UserFacingError("当前没有待输入的门槛字段")
    rule = _rule_model(editor, editor["rule"] | {field: positive_int(text, "门槛数值")})
    return editor | {"rule": rule.model_dump(), "field": None}


def _rule_model(editor: dict, value: dict | None = None):
    model = MessageAdmissionRule if editor.get("type") == "message" else InviteAdmissionRule
    if editor.get("type") not in {"message", "invite"}:
        raise UserFacingError("请先选择发言数或邀请好友门槛")
    return model.model_validate(value or {})


def _group(data: dict, chat_id: str):
    for group in data.get("participation_groups") or []:
        if str(group["id"]) == chat_id:
            return group
    raise UserFacingError("该参与群已移除，请重新选择")


def toggle_copy(data: dict, chat_id: str) -> dict:
    _group(data, chat_id)
    editor = dict(data[EDITOR_KEY])
    if chat_id == editor.get("chat_id"):
        raise UserFacingError("不能复制到来源群")
    selected = set(editor.get("copy_targets") or [])
    targets = selected - {chat_id} if chat_id in selected else selected | {chat_id}
    return data | {EDITOR_KEY: editor | {"copy_targets": sorted(targets)}}


def _confirmation_action(data, editor, *, operation):
    if operation == "save_and_continue":
        return _save_and_continue(data, editor)
    if operation == "confirm_change":
        pending = editor.get("pending_action")
        if not isinstance(pending, str):
            raise UserFacingError("切换确认已失效，请返回门槛设置")
        cleaned = {
            key: value
            for key, value in editor.items()
            if key not in {"pending_action", "chat_id", "rule", "rule_baseline", "field"}
        }
        if pending.startswith("draft_gate:mode:"):
            cleaned = cleaned | {"type": None, "groups": {}}
        return apply_action(data | {EDITOR_KEY: cleaned}, pending)
    if operation == "keep_editing":
        return data | {
            EDITOR_KEY: {key: value for key, value in editor.items() if key != "pending_action"}
        }


def _save_and_continue(data, editor):
    pending = str(editor.get("pending_action") or "")
    if pending != "draft_gate:list" and not pending.startswith("draft_gate:group:"):
        raise UserFacingError("本次切换不能暂存后继续，请明确选择门槛类型")
    changed = _save_group(data, editor, operation="group_save")
    changed.pop("pending_action", None)
    return apply_action(data | {EDITOR_KEY: changed}, pending)


def _save_admission(data, editor):
    ready_editor = checked_retention_editor(data)
    if uses_unlimited_policy(data) and ready_editor["retention_hours"] != 24:
        raise UserFacingError("新场邀请留存固定24小时，请返回当前门槛设置页")
    if editor["type"] not in {"message", "invite"}:
        raise UserFacingError("请先选择一种抽奖门槛")
    if not editor["groups"]:
        raise UserFacingError("请至少选择一个参与群并保存本群门槛，再保存整场门槛")
    if group_dirty(data):
        raise UserFacingError("请先保存本群设置，再保存整场门槛")
    return clear(data) | {
        "admission": {"type": editor["type"], "groups": editor["groups"]},
        "invite_retention_hours": ready_editor["retention_hours"],
        "base_activity_rules": {},
        "chat_message_thresholds": {},
    }


def _input_action(editor, *, parts):
    if parts[0] == "input" and parts[1] not in MESSAGE_FIELDS | INVITE_FIELDS:
        raise UserFacingError("门槛输入字段已失效")
    return editor | {"field": "search" if parts[0] == "search" else parts[1]}
