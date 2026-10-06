from __future__ import annotations

from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import GroupActivityRule, GroupInviteRule
from tg_lottery.telegram import creator_bonus_copy as bonus_copy
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_bonus_activation import activation_notice, save_label
from tg_lottery.telegram.creator_bonus_fields import (
    changed_positive as _changed_positive,
)
from tg_lottery.telegram.creator_bonus_fields import (
    input_label,
)
from tg_lottery.telegram.creator_bonus_fields import (
    positive_integer as _positive_integer,
)
from tg_lottery.telegram.creator_bonus_fields import (
    rule_key as _input_rule_key,
)
from tg_lottery.telegram.creator_number_controls import (
    DEFAULT_MESSAGE_COUNT,
    MESSAGE_COUNT_PRESETS,
    PRESET_PREFIX,
)
from tg_lottery.telegram.creator_number_controls import mode_row as _mode_row
from tg_lottery.telegram.creator_number_controls import number_row as _number_row
from tg_lottery.telegram.creator_number_controls import preset_row as _preset_row

ButtonRows = list[list[tuple[str, str]]]
PAGE_SIZE = 6
GROUP_PREFIXES = (
    "draft_bonus_invite_group:",
    "draft_bonus_activity_group:",
    "draft_bonus_group_page:",
)
DEFAULT_INVITES_PER_TICKET = 1
DEFAULT_DAILY_MESSAGES = DEFAULT_MESSAGE_COUNT
DAILY_REWARD_CYCLES = 1
GROUP_ACTIONS = frozenset(
    {
        "draft_bonus_invite_cycle_dec",
        "draft_bonus_invite_cycle_inc",
        "draft_bonus_invite_unlimited",
        "draft_bonus_invite_limited",
        "draft_bonus_invite_group_cap_dec",
        "draft_bonus_invite_group_cap_inc",
        "draft_bonus_invite_save",
        "draft_bonus_activity_cycle_dec",
        "draft_bonus_activity_cycle_inc",
        "draft_bonus_activity_group_reward_dec",
        "draft_bonus_activity_group_reward_inc",
        "draft_bonus_input_reward",
        "draft_bonus_activity_length_dec",
        "draft_bonus_activity_length_inc",
        "draft_bonus_activity_unlimited",
        "draft_bonus_activity_limited",
        "draft_bonus_activity_cap_dec",
        "draft_bonus_activity_cap_inc",
        "draft_bonus_activity_save",
        "draft_bonus_group_cancel",
        "draft_bonus_input_cancel",
        "draft_bonus_continue_editing",
        "draft_bonus_group_disable",
        "draft_bonus_input_cycle",
        "draft_bonus_input_length",
        "draft_bonus_input_cap",
        "draft_bonus_activity_daily",
    }
)


def handles_action(action: str) -> bool:
    return (
        action in GROUP_ACTIONS
        or action.startswith(GROUP_PREFIXES)
        or action.startswith(PRESET_PREFIX)
        or bonus_copy.handles_action(action)
    )


def apply_action(data: dict[str, Any], action: str) -> dict[str, Any]:
    if "length" in action:
        raise UserFacingError("字数设置已移除，请重新打开发言加成编辑页")
    _validate_v3_invite_action(data, action)
    direct = _apply_direct_action(data, action)
    if direct is not None:
        return direct
    working = dict(data.get("bonus_working_copy") or {})
    if not working:
        raise UserFacingError("加成编辑页已失效，请重新选择群")
    if action.endswith("_save"):
        return _save_working_copy(data, working)
    return data | {"bonus_working_copy": _change_working_copy(working, action)}


def _apply_direct_action(data: dict[str, Any], action: str) -> dict[str, Any] | None:
    if bonus_copy.handles_action(action):
        return bonus_copy.apply_action(data, action)
    if action.startswith("draft_bonus_group_page:"):
        return _change_page(data, action)
    if action.startswith(PRESET_PREFIX):
        return _apply_preset(data, action)
    if action.startswith(GROUP_PREFIXES[:2]):
        kind, chat_id = _parse_group_action(action)
        return data | {"bonus_working_copy": _new_working_copy(data, kind, chat_id)}
    if action == "draft_bonus_group_cancel":
        return _without_editor(data)
    if action == "draft_bonus_input_cancel":
        return data | {"bonus_input_field": None}
    if action == "draft_bonus_continue_editing":
        return dict(data)
    if action == "draft_bonus_group_disable":
        return _disable_working_rule(data)
    if action.startswith("draft_bonus_input_"):
        return data | {"bonus_input_field": action.rsplit("_", 1)[-1]}
    return None


def receive_input(data: dict[str, Any], text: str) -> dict[str, Any]:
    working = dict(data.get("bonus_working_copy") or {})
    field = str(data.get("bonus_input_field") or "")
    if not working or field not in {"cycle", "cap", "reward"}:
        raise UserFacingError("当前没有待输入的加成字段")
    if (
        data.get("participation_rules_version") == "v3.0"
        and working["kind"] == "invite"
        and field == "cap"
    ):
        raise UserFacingError("新活动邀请加成不限累计上限，请重新打开邀请加成编辑页")
    rule = dict(working["rule"])
    key = _input_rule_key(str(working["kind"]), field)
    rule[key] = _positive_integer(text)
    changed = working | {"rule": rule}
    if field == "cap":
        changed = changed | {"last_cap": rule[key]}
    return data | {
        "bonus_working_copy": changed,
        "bonus_input_field": None,
    }


def input_prompt(data: dict[str, Any]) -> str:
    working = data.get("bonus_working_copy") or {}
    label = input_label(str(working.get("kind")), str(data.get("bonus_input_field")))
    return f"请输入{label}（正整数）："


def working_copy_dirty(data: dict[str, Any]) -> bool:
    working = data.get("bonus_working_copy")
    if not isinstance(working, dict):
        return False
    kind = str(working["kind"])
    stored = (data.get(f"group_{kind}_rules") or {}).get(str(working["chat_id"]))
    return stored is None or dict(stored) != dict(working["rule"])


def summary(data: dict[str, Any]) -> str:
    invite = _enabled_rules(data, "invite")
    activity = _enabled_rules(data, "activity")
    if not invite and not activity:
        return "关闭"
    invite_unlimited = sum(rule.get("cap_mode") == "unlimited" for rule in invite)
    activity_unlimited = sum(rule.get("daily_cap_mode") == "unlimited" for rule in activity)
    return (
        f"邀请加成 {len(invite)} 个群（{invite_unlimited} 个不限）；"
        f"发言加成 {len(activity)} 个群（{activity_unlimited} 个不限）"
    )


def list_view(data: dict[str, Any], kind: str) -> tuple[str, ButtonRows]:
    groups = list(data.get("participation_groups") or [])
    page = _page(data, kind, len(groups))
    visible = groups[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = [
        [(_group_row_label(data, kind, group), f"draft_bonus_{kind}_group:{group['id']}")]
        for group in visible
    ]
    rows.extend(_page_rows(kind, page, len(groups)))
    if not groups:
        rows.append([("选择参与活动的群", "draft_group")])
    enabled = kind in data.get("bonus_types", [])
    rows.append([("关闭本加成" if enabled else "开启本加成", f"draft_bonus_toggle_{kind}")])
    if kind == "invite":
        rows.append([("有效邀请要求", "draft_retention")])
    rows.append([("↩ 返回加成设置", "draft_bonus_menu")])
    title = "邀请" if kind == "invite" else "发言"
    reminder = "机器人必须在每个参与群中且拥有管理员权限。"
    if not groups:
        reminder += "\n当前没有参与群，请先选择参与活动的群，再设置本群加成。"
    return f"{title}加成（逐群设置）\n\n{reminder}", rows


def editor_view(data: dict[str, Any], working: dict[str, Any]) -> tuple[str, ButtonRows]:
    if working["kind"] == "invite":
        return _invite_editor(data, working)
    return _activity_editor(data, working)


def page_kind(action: str) -> str:
    return action.split(":", 2)[1]


def _new_working_copy(data: dict[str, Any], kind: str, chat_id: int) -> dict[str, Any]:
    source = data.get(f"group_{kind}_rules") or {}
    stored = source.get(str(chat_id))
    rule = dict(stored or _default_rule(kind))
    rule["enabled"] = True
    cap_key = "max_bonus_tickets" if kind == "invite" else "daily_max_cycles"
    return {
        "kind": kind,
        "chat_id": chat_id,
        "rule": rule,
        "last_cap": rule.get(cap_key) or 1,
    }


def _change_working_copy(working: dict[str, Any], action: str) -> dict[str, Any]:
    rule = dict(working["rule"])
    if action == "draft_bonus_activity_daily":
        return working | {
            "rule": rule | {"daily_cap_mode": "limited", "daily_max_cycles": DAILY_REWARD_CYCLES}
        }
    key, delta = _numeric_change(action)
    if key:
        current = rule.get(key)
        if current is None:
            raise UserFacingError("请先选择设置上限")
        rule[key] = _changed_positive(int(current), delta)
        last_cap = rule[key] if key == _cap_keys(str(working["kind"]))[1] else working["last_cap"]
        return working | {"rule": rule, "last_cap": last_cap}
    if action.endswith("_unlimited"):
        return _unlimited_working(working, rule)
    if action.endswith("_limited"):
        return _limited_working(working, rule)
    return working


def _numeric_change(action: str) -> tuple[str | None, int]:
    delta = -1 if action.endswith("dec") else 1
    if "cycle" in action:
        key = "invites_per_cycle" if "invite" in action else "messages_per_cycle"
        return key, delta
    if "group_reward" in action:
        return "reward_tickets", delta
    if "cap" in action:
        key = "max_bonus_tickets" if "invite" in action else "daily_max_cycles"
        return key, delta
    return None, delta


def _unlimited_working(working: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    cap_mode, cap_key = _cap_keys(str(working["kind"]))
    last_cap = rule.get(cap_key) or working.get("last_cap") or 1
    return working | {
        "rule": rule | {cap_mode: "unlimited", cap_key: None},
        "last_cap": last_cap,
    }


def _limited_working(working: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    cap_mode, cap_key = _cap_keys(str(working["kind"]))
    cap = int(working.get("last_cap") or 1)
    return working | {"rule": rule | {cap_mode: "limited", cap_key: cap}}


def _save_working_copy(data: dict[str, Any], working: dict[str, Any]) -> dict[str, Any]:
    kind = str(working["kind"])
    rule = _validated_rule(kind, dict(working["rule"]) | {"enabled": True})
    key = f"group_{kind}_rules"
    rules = dict(data.get(key) or {})
    rules[str(working["chat_id"])] = rule
    types = list(dict.fromkeys([*data.get("bonus_types", []), kind]))
    updated = data | {key: rules, "bonus_types": types, "luck_mode": "standard"}
    return _without_editor(updated)


def _disable_working_rule(data: dict[str, Any]) -> dict[str, Any]:
    working = dict(data.get("bonus_working_copy") or {})
    if not working:
        raise UserFacingError("加成编辑页已失效，请重新选择群")
    kind = str(working["kind"])
    key = f"group_{kind}_rules"
    rules = dict(data.get(key) or {})
    stored = _validated_rule(kind, dict(working["rule"]) | {"enabled": False})
    rules[str(working["chat_id"])] = stored
    updated = data | {key: rules}
    if not any(rule.get("enabled") for rule in rules.values()):
        types = [value for value in data.get("bonus_types", []) if value != kind]
        updated |= {"bonus_types": types, "luck_mode": "standard" if types else "off"}
    return _without_editor(updated)


def _invite_editor(data: dict[str, Any], working: dict[str, Any]) -> tuple[str, ButtonRows]:
    rule = working["rule"]
    cap = _cap_label(rule, "invite")
    rows = [_number_row("邀请数", "invite_cycle", rule["invites_per_cycle"])]
    rows.append(_preset_row("cycle", (1, 2, 3, 5, 10), "人", selected=rule["invites_per_cycle"]))
    if data.get("participation_rules_version") != "v3.0":
        rows.append(_mode_row("invite", rule["cap_mode"]))
    if rule["cap_mode"] == "limited":
        rows.append(_number_row("上限", "invite_group_cap", cap))
        rows.append(_preset_row("cap", (1, 5, 10, 20), "次", selected=cap))
    rows.extend(_editor_footer("invite", data, working))
    title = _group_title(data, int(working["chat_id"]))
    text = f"👥 {title}邀请加成\n\n每 {rule['invites_per_cycle']} 个有效邀请 +1次；上限：{cap}"
    text += activation_notice(data, "invite", chat_id=int(working["chat_id"]))
    return text, rows


def _activity_editor(data: dict[str, Any], working: dict[str, Any]) -> tuple[str, ButtonRows]:
    rule = working["rule"]
    cap = _cap_label(rule, "activity")
    rows = [_number_row("消息数", "activity_cycle", rule["messages_per_cycle"])]
    rows.append(
        _preset_row("cycle", MESSAGE_COUNT_PRESETS, "条", selected=rule["messages_per_cycle"])
    )
    rows.append(_number_row("每轮奖励", "activity_group_reward", rule["reward_tickets"]))
    rows.append(_mode_row("activity", rule["daily_cap_mode"]))
    rows.append([("每天达标一次（按天累计）", "draft_bonus_activity_daily")])
    if rule["daily_cap_mode"] == "limited":
        rows.append(_number_row("上限", "activity_cap", cap))
        rows.append(_preset_row("cap", (1, 5, 10, 20), "轮", selected=cap))
    rows.extend(_editor_footer("activity", data, working))
    title = _group_title(data, int(working["chat_id"]))
    text = f"💬 {title}发言加成\n\n" + _activity_description(rule, cap)
    text += activation_notice(data, "activity", chat_id=int(working["chat_id"]))
    return text, rows


def _activity_description(rule: dict[str, Any], cap: object) -> str:
    reward = rule["reward_tickets"]
    if rule["daily_cap_mode"] == "limited" and rule["daily_max_cycles"] == DAILY_REWARD_CYCLES:
        counting = (
            f"每天发言满 {rule['messages_per_cycle']} 条 +{reward}次；每天只奖励一次，"
            "按实际达标天数累计，不要求连续。"
        )
    else:
        counting = f"每天每满 {rule['messages_per_cycle']} 条 +{reward}次；每日上限：{cap} 轮。"
    return (
        counting + "\n文字、图片、表情均计数，不限制字数。"
        "\n北京时间每天 00:00 更新当天统计，之前获得的加成保留。"
        "\n选择预设或点击“自定义”，保存后生效。"
    )


def _apply_preset(data: dict[str, Any], action: str) -> dict[str, Any]:
    working = dict(data.get("bonus_working_copy") or {})
    if not working:
        raise UserFacingError("加成编辑页已失效，请重新选择群")
    _, field, raw_value = action.split(":", 2)
    value = _positive_integer(raw_value)
    rule = dict(working["rule"])
    key = _input_rule_key(str(working["kind"]), field)
    rule[key] = value
    updated = working | {"rule": rule}
    if field == "cap":
        updated["last_cap"] = value
    return data | {"bonus_working_copy": updated}


def _editor_footer(kind: str, data: dict[str, Any], working: dict[str, Any]) -> ButtonRows:
    configured = str(working["chat_id"]) in (data.get(f"group_{kind}_rules") or {})
    rows = []
    if configured:
        rows.append([("复制设置到其他群", "draft_bonus_copy_start")])
    rows.append([(save_label(data, kind), f"draft_bonus_{kind}_save")])
    if configured:
        rows.append([("关闭该群加成", "draft_bonus_group_disable")])
    rows.append([("放弃本群修改", "draft_bonus_group_cancel")])
    return rows


def _page_rows(kind: str, page: int, count: int) -> ButtonRows:
    total_pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    buttons = []
    if page:
        buttons.append(("上一页", f"draft_bonus_group_page:{kind}:{page - 1}"))
    if page + 1 < total_pages:
        buttons.append(("下一页", f"draft_bonus_group_page:{kind}:{page + 1}"))
    return [buttons] if buttons else []


def _change_page(data: dict[str, Any], action: str) -> dict[str, Any]:
    _, kind, raw_page = action.split(":", 2)
    pages = dict(data.get("bonus_group_pages") or {})
    pages[kind] = int(raw_page)
    return data | {"bonus_group_pages": pages}


def _page(data: dict[str, Any], kind: str, count: int) -> int:
    maximum = max(0, (count - 1) // PAGE_SIZE)
    requested = int((data.get("bonus_group_pages") or {}).get(kind, 0))
    return min(maximum, max(0, requested))


def _group_row_label(data: dict[str, Any], kind: str, group: dict[str, Any]) -> str:
    rule = (data.get(f"group_{kind}_rules") or {}).get(str(group["id"]))
    title = chat_title(group)
    if not rule or not rule.get("enabled"):
        return f"⬜ {title}｜关闭"
    return f"✅ {title}｜{_rule_row_summary(kind, rule)}"


def _rule_row_summary(kind: str, rule: dict[str, Any]) -> str:
    if kind == "invite":
        cap = "不限" if rule["cap_mode"] == "unlimited" else f"上限{rule['max_bonus_tickets']}"
        return f"每{rule['invites_per_cycle']}人+1｜{cap}"
    cap = (
        "每日不限" if rule["daily_cap_mode"] == "unlimited" else f"日上限{rule['daily_max_cycles']}"
    )
    return f"{rule['messages_per_cycle']}条｜{cap}"


def _group_title(data: dict[str, Any], chat_id: int) -> str:
    for group in data.get("participation_groups") or []:
        if int(group["id"]) == chat_id:
            return chat_title(group)
    return chat_title({"id": chat_id})


def _parse_group_action(action: str) -> tuple[str, int]:
    prefix, raw_chat_id = action.split(":", 1)
    kind = "invite" if "invite_group" in prefix else "activity"
    return kind, int(raw_chat_id)


def _default_rule(kind: str) -> dict[str, Any]:
    if kind == "invite":
        return GroupInviteRule(invites_per_cycle=DEFAULT_INVITES_PER_TICKET).model_dump(mode="json")
    return GroupActivityRule(messages_per_cycle=DEFAULT_DAILY_MESSAGES).model_dump(mode="json")


def _validated_rule(kind: str, rule: dict[str, Any]) -> dict[str, Any]:
    model = GroupInviteRule if kind == "invite" else GroupActivityRule
    return model.model_validate(rule).model_dump(mode="json")


def _cap_keys(kind: str) -> tuple[str, str]:
    if kind == "invite":
        return "cap_mode", "max_bonus_tickets"
    return "daily_cap_mode", "daily_max_cycles"


def _cap_label(rule: dict[str, Any], kind: str) -> str:
    mode_key, cap_key = _cap_keys(kind)
    return "不设上限" if rule[mode_key] == "unlimited" else str(rule[cap_key])


def _without_editor(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    result.pop("bonus_working_copy", None)
    result.pop("bonus_input_field", None)
    return result


def _enabled_rules(data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if data.get("participation_rules_version") == "v3.0" and kind not in data.get(
        "bonus_types", []
    ):
        return []
    return [
        rule for rule in (data.get(f"group_{kind}_rules") or {}).values() if rule.get("enabled")
    ]


def _validate_v3_invite_action(data, action):
    if data.get("participation_rules_version") != "v3.0":
        return
    kind = (data.get("bonus_working_copy") or {}).get("kind")
    invite_action = "invite" in action or kind == "invite"
    cap_action = "cap" in action or ("limited" in action and "unlimited" not in action)
    if invite_action and cap_action:
        raise UserFacingError("本场邀请加成固定不限累计上限")
