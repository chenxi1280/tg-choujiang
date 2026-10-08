from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import BaseActivityRule
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_number_controls import DEFAULT_MESSAGE_COUNT, MESSAGE_COUNT_PRESETS
from tg_lottery.telegram.creator_schedule import positive_int

ACTION_PREFIX = "draft_base:"
DEFAULT_MESSAGES = DEFAULT_MESSAGE_COUNT
DEFAULT_DAYS = 1
FIELDS = {
    "daily_messages": ("每天发言条数", "条", MESSAGE_COUNT_PRESETS),
    "consecutive_days": ("连续天数", "天", (1, 3, 7)),
}
EDITOR_KEY = "base_activity_editor"


def clear_editor(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key != EDITOR_KEY}


def group_for(data: dict[str, Any], chat_id: str) -> dict[str, Any]:
    for group in data.get("participation_groups") or []:
        if str(group["id"]) == chat_id:
            return group
    raise UserFacingError("该参与群已移除，请返回重新选择群")


def apply_action(data: dict[str, Any], action: str) -> dict[str, Any]:
    parts = action.removeprefix(ACTION_PREFIX).split(":")
    operation = parts[0]
    if operation == "group":
        return _open_group(data, parts[1])
    if operation == "list":
        return clear_editor(data)
    editor = dict(data.get(EDITOR_KEY) or {})
    if not editor:
        raise UserFacingError("请先选择要设置发言门槛的群")
    group_for(data, editor["chat_id"])
    if operation in {"save", "disable"}:
        return _save(data, editor, disable=operation == "disable")
    if operation == "cancel_input":
        return data | {EDITOR_KEY: editor | {"field": None}}
    return _field_action(data, editor, parts=parts)


def _field_action(data: dict[str, Any], editor: dict[str, Any], *, parts: list[str]) -> dict:
    operation = parts[0]
    if operation == "input" and parts[1] in FIELDS:
        return data | {EDITOR_KEY: editor | {"field": parts[1]}}
    if operation == "preset" and parts[1] in FIELDS:
        return _set_value(data, editor, field=parts[1], text=parts[2])
    raise UserFacingError("发言门槛操作已失效，请返回重新设置")


def receive_value(data: dict[str, Any], text: str) -> dict[str, Any]:
    editor = dict(data.get(EDITOR_KEY) or {})
    field = editor.get("field")
    if field not in FIELDS:
        raise UserFacingError("请点击需要自定义的条数或天数")
    group_for(data, editor["chat_id"])
    return _set_value(data, editor, field=field, text=text)


def _open_group(data: dict[str, Any], chat_id: str) -> dict[str, Any]:
    group_for(data, chat_id)
    stored = (data.get("base_activity_rules") or {}).get(chat_id)
    rule = (
        stored
        or BaseActivityRule(
            consecutive_days=DEFAULT_DAYS,
            daily_messages=DEFAULT_MESSAGES,
        ).model_dump()
    )
    return data | {EDITOR_KEY: {"chat_id": chat_id, "rule": dict(rule), "field": None}}


def _set_value(
    data: dict[str, Any], editor: dict[str, Any], *, field: str, text: str
) -> dict[str, Any]:
    value = positive_int(text, FIELDS[field][0])
    rule = BaseActivityRule.model_validate(editor["rule"] | {field: value})
    return data | {EDITOR_KEY: editor | {"rule": rule.model_dump(), "field": None}}


def _save(data: dict[str, Any], editor: dict[str, Any], *, disable: bool) -> dict[str, Any]:
    rules = dict(data.get("base_activity_rules") or {})
    key = editor["chat_id"]
    if disable:
        rules = {chat_id: rule for chat_id, rule in rules.items() if chat_id != key}
    else:
        rules = rules | {key: BaseActivityRule.model_validate(editor["rule"]).model_dump()}
    return clear_editor(data) | {"base_activity_rules": rules}


def list_rows(data: dict[str, Any]) -> list:
    rules = data.get("base_activity_rules") or {}
    rows = []
    for group in data.get("participation_groups") or []:
        rule = rules.get(str(group["id"]))
        status = f"每天 {rule['daily_messages']} 条" if rule else "未设置"
        rows.append([(f"{chat_title(group)} · {status}", f"draft_base:group:{group['id']}")])
    rows.append([("↩ 返回准入条件", "draft_participation")])
    return rows


def editor_view(data: dict[str, Any]) -> tuple[str, list]:
    editor = data[EDITOR_KEY]
    title = chat_title(group_for(data, editor["chat_id"]))
    field = editor.get("field")
    if field:
        return (
            f"{title} · 自定义{FIELDS[field][0]}\n\n请发送一个正整数：",
            [[("取消输入", "draft_base:cancel_input")]],
        )
    rule = editor["rule"]
    text = (
        f"🚪 {title} · 基础发言门槛\n\n"
        f"连续 {rule['consecutive_days']} 天，每天至少 {rule['daily_messages']} 条，"
        "文字、图片、表情均计数。\n"
        "点击参与链接登记后开始统计；全部配置群达标后获得抽奖资格。\n"
        "北京时间每天 00:00 更新当天统计，完成后保留本场资格。\n\n"
        "选择预设或自定义数值，点击保存后生效。"
    )
    rows = [_field_row(field) for field in FIELDS]
    rows.append([("✅ 保存设置", "draft_base:save")])
    if editor["chat_id"] in (data.get("base_activity_rules") or {}):
        rows.append([("关闭该群门槛", "draft_base:disable")])
    rows.append([("↩ 返回群列表（不保存）", "draft_base:list")])
    return text, rows


def _field_row(field: str) -> list[tuple[str, str]]:
    label, unit, values = FIELDS[field]
    return [
        *((f"{value}{unit}", f"draft_base:preset:{field}:{value}") for value in values),
        (f"自定义{label}", f"draft_base:input:{field}"),
    ]
