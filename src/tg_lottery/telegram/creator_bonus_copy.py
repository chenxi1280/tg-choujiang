from __future__ import annotations

from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.keyboard_layout import list_rows

ButtonRows = list[list[tuple[str, str]]]
PAGE_SIZE = 6
TOGGLE_PREFIX = "draft_bonus_copy_toggle:"
PAGE_PREFIX = "draft_bonus_copy_page:"
COPY_ACTIONS = frozenset(
    {
        "draft_bonus_copy_start",
        "draft_bonus_copy_all",
        "draft_bonus_copy_clear",
        "draft_bonus_copy_continue",
        "draft_bonus_copy_back",
        "draft_bonus_copy_confirm",
        "draft_bonus_copy_cancel",
        "draft_bonus_copy_noop",
    }
)


def handles_action(action: str) -> bool:
    return action in COPY_ACTIONS or action.startswith((TOGGLE_PREFIX, PAGE_PREFIX))


def apply_action(data: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "draft_bonus_copy_start":
        return _start_copy(data)
    copy_state = _required_copy_state(data)
    if action == "draft_bonus_copy_noop":
        return dict(data)
    if action.startswith(TOGGLE_PREFIX):
        return _toggle_target(data, copy_state, int(action.removeprefix(TOGGLE_PREFIX)))
    if action.startswith(PAGE_PREFIX):
        return _change_page(data, copy_state, action)
    if action == "draft_bonus_copy_all":
        return _select_visible(data, copy_state)
    if action == "draft_bonus_copy_clear":
        return _with_copy(data, copy_state | {"targets": []})
    if action == "draft_bonus_copy_continue":
        return _continue_copy(data, copy_state)
    if action == "draft_bonus_copy_back":
        return _with_copy(data, copy_state | {"confirm": False})
    if action == "draft_bonus_copy_confirm":
        return _confirm_copy(data, copy_state)
    return _cancel_copy(data, copy_state)


def copy_view(data: dict[str, Any]) -> tuple[str, ButtonRows]:
    copy_state = _required_copy_state(data)
    if copy_state.get("confirm"):
        return _confirmation_view(data, copy_state)
    return _selection_view(data, copy_state)


def _start_copy(data: dict[str, Any]) -> dict[str, Any]:
    working = dict(data.get("bonus_working_copy") or {})
    if not working:
        raise UserFacingError("请先打开一个群的加成设置")
    kind = str(working["kind"])
    source_id = str(working["chat_id"])
    stored = (data.get(f"group_{kind}_rules") or {}).get(source_id)
    if stored is None or dict(stored) != dict(working["rule"]):
        raise UserFacingError("请先保存当前群设置，再复制到其他群")
    copy_state = {
        "kind": kind,
        "source_chat_id": int(source_id),
        "targets": [],
        "page": 0,
        "confirm": False,
    }
    result = dict(data)
    result.pop("bonus_working_copy", None)
    result.pop("bonus_input_field", None)
    result["bonus_copy"] = copy_state
    return result


def _toggle_target(
    data: dict[str, Any], copy_state: dict[str, Any], chat_id: int
) -> dict[str, Any]:
    candidates = {int(group["id"]) for group in _target_groups(data, copy_state)}
    if chat_id not in candidates:
        raise UserFacingError("复制目标不属于当前参与群")
    targets = set(int(value) for value in copy_state.get("targets", []))
    targets.symmetric_difference_update({chat_id})
    return _with_copy(data, copy_state | {"targets": sorted(targets)})


def _change_page(data: dict[str, Any], copy_state: dict[str, Any], action: str) -> dict[str, Any]:
    delta = -1 if action.endswith("prev") else 1
    maximum = max(0, (len(_target_groups(data, copy_state)) - 1) // PAGE_SIZE)
    page = min(maximum, max(0, int(copy_state.get("page", 0)) + delta))
    return _with_copy(data, copy_state | {"page": page})


def _select_visible(data: dict[str, Any], copy_state: dict[str, Any]) -> dict[str, Any]:
    targets = set(int(value) for value in copy_state.get("targets", []))
    targets.update(int(group["id"]) for group in _visible_groups(data, copy_state))
    return _with_copy(data, copy_state | {"targets": sorted(targets)})


def _continue_copy(data: dict[str, Any], copy_state: dict[str, Any]) -> dict[str, Any]:
    if not copy_state.get("targets"):
        raise UserFacingError("请至少选择一个复制目标群")
    return _with_copy(data, copy_state | {"confirm": True})


def _confirm_copy(data: dict[str, Any], copy_state: dict[str, Any]) -> dict[str, Any]:
    kind = str(copy_state["kind"])
    rules = dict(data.get(f"group_{kind}_rules") or {})
    source = rules.get(str(copy_state["source_chat_id"]))
    if source is None:
        raise UserFacingError("来源群设置已失效，请重新配置")
    for chat_id in copy_state["targets"]:
        rules[str(chat_id)] = dict(source)
    result = dict(data)
    result[f"group_{kind}_rules"] = rules
    result["bonus_copy_result_kind"] = kind
    result.pop("bonus_copy", None)
    return result


def _cancel_copy(data: dict[str, Any], copy_state: dict[str, Any]) -> dict[str, Any]:
    kind = str(copy_state["kind"])
    chat_id = int(copy_state["source_chat_id"])
    stored = (data.get(f"group_{kind}_rules") or {}).get(str(chat_id))
    result = dict(data)
    result.pop("bonus_copy", None)
    if stored is not None:
        result["bonus_working_copy"] = {
            "kind": kind,
            "chat_id": chat_id,
            "rule": dict(stored),
            "last_cap": _last_cap(kind, stored),
        }
    return result


def _selection_view(data: dict[str, Any], copy_state: dict[str, Any]) -> tuple[str, ButtonRows]:
    kind = str(copy_state["kind"])
    targets = {int(value) for value in copy_state.get("targets", [])}
    rows = [
        [
            (
                _target_label(data, kind, group, targets=targets),
                f"{TOGGLE_PREFIX}{group['id']}",
            )
        ]
        for group in _visible_groups(data, copy_state)
    ]
    details = "\n".join(
        _target_label(data, kind, group, targets=targets)
        for group in _visible_groups(data, copy_state)
    )
    rows = list_rows(rows, start=int(copy_state.get("page", 0)) * PAGE_SIZE + 1)
    rows.append([("全选本页", "draft_bonus_copy_all"), ("清空已选", "draft_bonus_copy_clear")])
    rows.extend(_page_rows(data, copy_state))
    rows.append([("继续", "draft_bonus_copy_continue"), ("取消", "draft_bonus_copy_cancel")])
    source = _group_title(data, int(copy_state["source_chat_id"]))
    title = "邀请" if kind == "invite" else "发言"
    return f"把「{source}」{title}设置复制到：\n\n已选择 {len(targets)} 个群\n{details}", rows


def _confirmation_view(data: dict[str, Any], copy_state: dict[str, Any]) -> tuple[str, ButtonRows]:
    count = len(copy_state["targets"])
    kind = str(copy_state["kind"])
    rule = (data.get(f"group_{kind}_rules") or {})[str(copy_state["source_chat_id"])]
    summary = _rule_summary(kind, rule)
    text = f"将覆盖 {count} 个群的{_kind_title(kind)}设置。\n\n来源规则：{summary}"
    rows = [
        [(f"确认覆盖 {count} 个群", "draft_bonus_copy_confirm")],
        [("返回选择", "draft_bonus_copy_back"), ("取消", "draft_bonus_copy_cancel")],
    ]
    return text, rows


def _target_groups(data: dict[str, Any], copy_state: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = int(copy_state["source_chat_id"])
    return [
        group for group in data.get("participation_groups") or [] if int(group["id"]) != source_id
    ]


def _visible_groups(data: dict[str, Any], copy_state: dict[str, Any]) -> list[dict[str, Any]]:
    page = int(copy_state.get("page", 0))
    groups = _target_groups(data, copy_state)
    return groups[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]


def _page_rows(data: dict[str, Any], copy_state: dict[str, Any]) -> ButtonRows:
    count = len(_target_groups(data, copy_state))
    total = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    if total == 1:
        return []
    page = int(copy_state.get("page", 0))
    return [
        [
            ("← 上一页", f"{PAGE_PREFIX}prev"),
            (f"{page + 1}/{total}", "draft_bonus_copy_noop"),
            ("下一页 →", f"{PAGE_PREFIX}next"),
        ]
    ]


def _target_label(
    data: dict[str, Any], kind: str, group: dict[str, Any], *, targets: set[int]
) -> str:
    chat_id = int(group["id"])
    marker = "✅" if chat_id in targets else "⬜"
    existing = chat_id in {int(value) for value in (data.get(f"group_{kind}_rules") or {})}
    status = "将覆盖现有设置" if existing else "当前关闭"
    return f"{marker} {chat_title(group)}｜{status}"


def _rule_summary(kind: str, rule: dict[str, Any]) -> str:
    if kind == "invite":
        cap = (
            "不设上限"
            if rule["cap_mode"] == "unlimited"
            else f"上限 {rule['max_bonus_tickets']} 次"
        )
        return f"每 {rule['invites_per_cycle']} 人 +1，{cap}"
    cap = (
        "每日不限"
        if rule["daily_cap_mode"] == "unlimited"
        else f"每日上限 {rule['daily_max_cycles']} 次"
    )
    return (
        f"每 {rule['messages_per_cycle']} 条 +{rule.get('reward_tickets', 1)}、"
        f"文字、图片、表情均计数，{cap}"
    )


def _last_cap(kind: str, rule: dict[str, Any]) -> int:
    key = "max_bonus_tickets" if kind == "invite" else "daily_max_cycles"
    return int(rule.get(key) or 1)


def _kind_title(kind: str) -> str:
    return "邀请加成" if kind == "invite" else "活跃加成（人品）"


def _group_title(data: dict[str, Any], chat_id: int) -> str:
    for group in data.get("participation_groups") or []:
        if int(group["id"]) == chat_id:
            return chat_title(group)
    return chat_title({"id": chat_id})


def _required_copy_state(data: dict[str, Any]) -> dict[str, Any]:
    copy_state = data.get("bonus_copy")
    if not isinstance(copy_state, dict):
        raise UserFacingError("复制设置页已失效，请重新进入")
    return dict(copy_state)


def _with_copy(data: dict[str, Any], copy_state: dict[str, Any]) -> dict[str, Any]:
    return data | {"bonus_copy": copy_state}
