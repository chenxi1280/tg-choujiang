from typing import Any

from tg_lottery.domain.admission import InviteAdmissionRule, MessageAdmissionRule
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_behavior_editor import EDITOR_KEY, PAGE_SIZE
from tg_lottery.telegram.creator_number_controls import MESSAGE_COUNT_PRESETS
from tg_lottery.telegram.keyboard_layout import fit_rows, list_rows

FIELD_LABELS = {
    "daily_messages": "每日条数",
    "consecutive_days": "连续天数",
    "required_valid_invites": "有效邀请人数",
}
PRESETS = {
    "daily_messages": MESSAGE_COUNT_PRESETS,
    "consecutive_days": (1, 3, 7),
    "required_valid_invites": MESSAGE_COUNT_PRESETS,
}
MODE_LABELS = {"message": "发言数", "invite": "邀请好友"}


def admission_summary(data: dict[str, Any]) -> str:
    gate = data.get("admission") or {}
    groups = gate.get("groups") or {}
    names = {str(item["id"]): chat_title(item) for item in data.get("participation_groups") or []}
    title = "发言数" if gate.get("type") == "message" else "邀请好友"
    if not gate.get("type"):
        return "🚪 抽奖门槛：请选一种类型并配置至少一个群\n" + "\n".join(
            data.get("import_notes") or []
        )
    if not groups:
        return f"🚪 抽奖门槛：{title}（请配置至少一个群）"
    lines = [f"🚪 抽奖门槛：{title}（配置群必须分别全部达标）"]
    lines.extend(
        f"· {names.get(key, key)}：{rule_text(rule, gate['type'])}" for key, rule in groups.items()
    )
    lines.append("本场门槛完成一次即可；未选群无行为门槛，全部参与群仍须加入。")
    return "\n".join(lines)


def rule_text(rule: dict, mode: str) -> str:
    if mode == "invite":
        parsed = InviteAdmissionRule.model_validate(rule)
        return f"有效新成员 {parsed.required_valid_invites} 人；人数继续累计"
    parsed = MessageAdmissionRule.model_validate(rule)
    return (
        f"连续 {parsed.consecutive_days} 天，每天 {parsed.daily_messages} 条，"
        "文字、图片、表情均计数；北京时间自然日"
    )


def view(data: dict) -> tuple[str, list]:
    text, rows = _view(data)
    notice = (data.get(EDITOR_KEY) or {}).get("notice")
    return (notice + "\n\n" + text if notice else text), rows


def _view(data: dict) -> tuple[str, list]:
    editor = data[EDITOR_KEY]
    if editor.get("pending_action"):
        return _pending_view(editor)
    if editor.get("field"):
        field = editor["field"]
        text = (
            "请输入群名关键词："
            if field == "search"
            else _numeric_input_text(data, editor, field=field)
        )
        rows = [] if field == "search" else fit_rows([_field_preset_row(editor, field)])
        return text, [*rows, [("取消输入", "draft_gate:cancel_input")]]
    if "copy_targets" in editor:
        return _copy_view(data, editor)
    if editor.get("chat_id"):
        return _group_view(data, editor)
    return _list_view(data, editor)


def _numeric_input_text(data, editor, *, field):
    group = next(
        (g for g in data.get("participation_groups", []) if str(g["id"]) == editor.get("chat_id")),
        None,
    )
    title = chat_title(group) if group is not None else "当前参与群"
    value = (editor.get("rule") or {}).get(field, "未设置")
    label = FIELD_LABELS[field]
    return (
        f"🚪 抽奖门槛 · {title}\n当前{label}：{value}\n\n请输入{label}（正整数，只发送数字）："
        "\n💾 回复数字或点快捷值，核验通过后直接保存本群到草稿。"
        "\n↩️ 取消输入保留原值及其他群的暂存设置。"
    )


def _pending_view(editor):
    pending = editor["pending_action"]
    rows = []
    if pending == "draft_gate:list" or pending.startswith("draft_gate:group:"):
        rows.append([("暂存本群并继续", "draft_gate:save_and_continue")])
    rows.extend(
        [
            [("确认放弃并继续", "draft_gate:confirm_change")],
            [("继续编辑", "draft_gate:keep_editing")],
        ]
    )
    return "本群修改尚未暂存，或切换类型将清除已配置门槛。请选择如何继续。", rows


def _list_view(data, editor):
    mode = editor.get("type")
    gate = {"type": mode, "groups": editor.get("groups") or {}}
    text = "🚪 抽奖门槛（只能选择一种）\n\n" + admission_summary(data | {"admission": gate})
    text += "\n\n" + _selection_hint(data, mode)
    visible, page, _ = _visible_groups(data, editor)
    text += "\n\n" + "\n".join(
        f"{page * PAGE_SIZE + i}. {chat_title(item)}" for i, item in enumerate(visible, 1)
    )
    text += _search_summary(data, editor)
    original = (data.get("admission") or {}).get("type")
    if original and original != mode:
        text += "\n保存将替换原门槛；两种加成保持独立设置。"
    rows = [
        [
            (("✅ " if mode == value else "") + label, f"draft_gate:mode:{value}")
            for value, label in MODE_LABELS.items()
        ]
    ]
    rows.extend(_group_rows(data, editor))
    if mode == "invite":
        rows.append([("有效邀请要求", "draft_gate:retention")])
    return text, [*rows, *_list_footer(data, gate)]


def _list_footer(data, gate):
    if gate == (data.get("admission") or {}) and gate["groups"]:
        return [[("返回预览（已保存）", "draft_gate:leave")]]
    return [[("保存本场门槛", "draft_gate:save")], [("返回预览", "draft_gate:leave")]]


def _selection_hint(data: dict, mode: str | None) -> str:
    if mode is None:
        return "当前尚未选择类型，请点击「发言数」或「邀请好友」。"
    selected = f"当前选择：{MODE_LABELS[mode]}。"
    if not data.get("participation_groups"):
        return selected + "\n请点击「选择参与活动的群」，选好群后回来设置门槛。"
    fields = "每日条数和连续天数" if mode == "message" else "有效邀请人数"
    return selected + f"\n请点击下方群名设置{fields}；快捷值或回复数字直接保存本群到草稿。"


def _group_view(data: dict, editor: dict) -> tuple[str, list]:
    group = next(
        item for item in data["participation_groups"] if str(item["id"]) == editor["chat_id"]
    )
    mode = editor["type"]
    fields = (
        ("daily_messages", "consecutive_days") if mode == "message" else ("required_valid_invites",)
    )
    rows = fit_rows([_field_preset_row(editor, field) for field in fields])
    rows.extend(
        [
            [("保存本群门槛", "draft_gate:save_group")],
            [("移除本群门槛", "draft_gate:disable")],
            [("返回群列表", "draft_gate:list")],
        ]
    )
    if len(data["participation_groups"]) > 1:
        rows.insert(-1, [("复制到选定群", "draft_gate:copy_open")])
    return (
        f"🚪 抽奖门槛 · {chat_title(group)}\n{rule_text(editor['rule'], mode)}\n"
        "🌱 每日加成独立设置。\n\n💾 快捷值或直接回复正整数，即保存本群门槛到草稿。\n"
        "默认修改每日条数；连续天数请点对应自定义。"
        if mode == "message"
        else f"🚪 抽奖门槛 · {chat_title(group)}\n{rule_text(editor['rule'], mode)}\n\n"
        "💾 快捷值或直接回复正整数，即保存本群有效邀请人数到草稿。",
        rows,
    )


def _visible_groups(data: dict, editor: dict):
    keyword = str(editor.get("search") or "").casefold()
    groups = [
        item
        for item in data.get("participation_groups") or []
        if keyword in chat_title(item).casefold()
        and ("copy_targets" not in editor or str(item["id"]) != editor["chat_id"])
    ]
    page = min(int(editor.get("page", 0)), max(0, (len(groups) - 1) // PAGE_SIZE))
    return groups[page * PAGE_SIZE : (page + 1) * PAGE_SIZE], page, len(groups)


def _group_rows(data: dict, editor: dict) -> list:
    if not data.get("participation_groups"):
        return [[("选择参与活动的群", "draft_gate:choose_groups")]]
    groups, page, count = _visible_groups(data, editor)
    rows = [
        [
            (
                chat_title(item)
                + (" · 已设置" if str(item["id"]) in editor["groups"] else " · 不设门槛"),
                f"draft_gate:group:{item['id']}",
            )
        ]
        for item in groups
    ]
    navigation = []
    if page:
        navigation.append(("上一页", f"draft_gate:page:{page - 1}"))
    if (page + 1) * PAGE_SIZE < count:
        navigation.append(("下一页", f"draft_gate:page:{page + 1}"))
    rows.extend([navigation] if navigation else [])
    rows = [*list_rows(rows[: len(groups)], start=page * PAGE_SIZE + 1), *rows[len(groups) :]]
    rows.extend(_search_rows(editor, page))
    return rows


def _search_rows(editor: dict, page: int) -> list:
    rows = [[("搜索群", "draft_gate:search"), ("刷新", f"draft_gate:page:{page}")]]
    if editor.get("search"):
        rows.append([("清除搜索", "draft_gate:clear_search")])
    return rows


def _search_summary(data: dict, editor: dict) -> str:
    keyword = editor.get("search")
    if not keyword:
        return ""
    _, _, count = _visible_groups(data, editor)
    text = f"\n搜索：{keyword}"
    if not count:
        text += "\n没有匹配的群，请清除搜索后重新选择。"
    return text


def _copy_view(data: dict, editor: dict) -> tuple[str, list]:
    groups, page, count = _visible_groups(data, editor)
    selected = editor["copy_targets"]
    rows = [
        [
            (
                ("✅ " if str(item["id"]) in selected else "⬜ ") + chat_title(item),
                f"draft_gate:copy_toggle:{item['id']}",
            )
        ]
        for item in groups
    ]
    navigation = []
    if page:
        navigation.append(("上一页", f"draft_gate:page:{page - 1}"))
    if (page + 1) * PAGE_SIZE < count:
        navigation.append(("下一页", f"draft_gate:page:{page + 1}"))
    rows.extend([navigation] if navigation else [])
    rows.extend(_search_rows(editor, page))
    if selected:
        rows.append([("确认覆盖选定群", "draft_gate:copy_confirm")])
    rows.append([("取消复制", "draft_gate:copy_cancel")])
    names = {str(item["id"]): chat_title(item) for item in data["participation_groups"]}
    text = (
        f"来源群 {names[editor['chat_id']]}\n{rule_text(editor['rule'], editor['type'])}\n"
        f"将覆盖 {len(selected)} 个选定群的同类门槛（加成不变）：\n"
        + "、".join(names[key] for key in selected)
    )
    text += _search_summary(data, editor)
    text += "\n可选群：\n" + "\n".join(
        f"{page * PAGE_SIZE + i}. {chat_title(item)}" for i, item in enumerate(groups, 1)
    )
    text += _copy_hint(editor, count=count, selected=selected)
    return text, [*list_rows(rows[: len(groups)], start=page * PAGE_SIZE + 1), *rows[len(groups) :]]


def _copy_hint(editor, *, count, selected):
    if not count and not editor.get("search"):
        return "\n没有其他参与群可复制，请取消复制返回本群设置。"
    return "\n请先勾选目标群，再确认覆盖。" if not selected else ""


def _field_preset_row(editor: dict, field: str) -> list:
    units = {"daily_messages": "条/天", "consecutive_days": "天", "required_valid_invites": "人"}
    current = editor.get("rule", {}).get(field)
    values = [
        (
            f"{'✓ ' if value == current else ''}{value}{units[field]}",
            f"draft_gate:quick:{field}:{value}",
        )
        for value in PRESETS[field]
    ]
    return [*values, ("自定义" + FIELD_LABELS[field], f"draft_gate:input:{field}")]
