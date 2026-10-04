from typing import Any

from tg_lottery.domain.admission import InviteAdmissionRule, MessageAdmissionRule
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_behavior_editor import EDITOR_KEY, PAGE_SIZE

FIELD_LABELS = {
    "daily_messages": "每日条数",
    "consecutive_days": "连续天数",
    "min_text_length": "最低有效字数",
    "required_valid_invites": "有效邀请人数",
}
PRESETS = {
    "daily_messages": (2, 20, 34),
    "consecutive_days": (1, 3, 7),
    "min_text_length": (1, 4, 5),
    "required_valid_invites": (1, 2, 5),
}


def admission_summary(data: dict[str, Any]) -> str:
    gate = data.get("admission") or {}
    groups = gate.get("groups") or {}
    names = {str(item["id"]): chat_title(item) for item in data.get("participation_groups") or []}
    title = "发言数" if gate.get("type") == "message" else "邀请好友"
    if not gate.get("type") or not groups:
        return "🎟 抽奖门槛：请选一种类型并配置至少一个群\n" + "\n".join(
            data.get("import_notes") or []
        )
    lines = [f"🎟 抽奖门槛：{title}（配置群必须分别全部达标）"]
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
        f"每条至少 {parsed.min_text_length} 个有效字符；北京时间自然日"
    )


def view(data: dict) -> tuple[str, list]:
    editor = data[EDITOR_KEY]
    if editor.get("pending_action"):
        return (
            "本群修改尚未保存，或切换类型将清除已配置门槛。确认放弃这些修改？",
            [
                [("确认放弃并继续", "draft_gate:confirm_change")],
                [("继续编辑", "draft_gate:keep_editing")],
            ],
        )
    if editor.get("field"):
        field = editor["field"]
        text = (
            "请输入群名关键词："
            if field == "search"
            else f"请输入{FIELD_LABELS[field]}（正整数）："
        )
        return text, [[("取消输入", "draft_gate:cancel_input")]]
    if "copy_targets" in editor:
        return _copy_view(data, editor)
    if editor.get("chat_id"):
        return _group_view(data, editor)
    return _list_view(data, editor)


def _list_view(data, editor):
    mode = editor.get("type")
    gate = {"type": mode, "groups": editor.get("groups") or {}}
    text = "抽奖门槛（只能选择一种）\n\n" + admission_summary(data | {"admission": gate})
    original = (data.get("admission") or {}).get("type")
    if original and original != mode:
        text += "\n保存将替换原门槛；两种加成保持独立设置。"
    rows = [[("发言数", "draft_gate:mode:message"), ("邀请好友", "draft_gate:mode:invite")]]
    rows.extend(_group_rows(data, editor))
    if mode == "invite":
        rows.append([("有效邀请要求", "draft_gate:retention")])
    return text, [*rows, [("保存门槛", "draft_gate:save")], [("返回预览", "draft_gate:leave")]]


def _group_view(data: dict, editor: dict) -> tuple[str, list]:
    group = next(
        item for item in data["participation_groups"] if str(item["id"]) == editor["chat_id"]
    )
    mode = editor["type"]
    fields = tuple(PRESETS)[:3] if mode == "message" else ("required_valid_invites",)
    rows = [
        [
            *((str(value), f"draft_gate:preset:{field}:{value}") for value in PRESETS[field]),
            ("自定义" + FIELD_LABELS[field], f"draft_gate:input:{field}"),
        ]
        for field in fields
    ]
    rows.extend(
        [
            [("保存本群", "draft_gate:group_save")],
            [("移除本群门槛", "draft_gate:disable")],
            [("复制到选定群", "draft_gate:copy_open")],
            [("返回群列表", "draft_gate:list")],
        ]
    )
    return f"{chat_title(group)}\n{rule_text(editor['rule'], mode)}\n每日加成独立设置。", rows


def _visible_groups(data: dict, editor: dict):
    keyword = str(editor.get("search") or "").casefold()
    groups = [
        item
        for item in data.get("participation_groups") or []
        if keyword in chat_title(item).casefold()
    ]
    page = min(int(editor.get("page", 0)), max(0, (len(groups) - 1) // PAGE_SIZE))
    return groups[page * PAGE_SIZE : (page + 1) * PAGE_SIZE], page, len(groups)


def _group_rows(data: dict, editor: dict) -> list:
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
    rows.append([("搜索群", "draft_gate:search"), ("刷新", f"draft_gate:page:{page}")])
    return rows


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
        if str(item["id"]) != editor["chat_id"]
    ]
    if page:
        rows.append([("上一页", f"draft_gate:page:{page - 1}")])
    if (page + 1) * PAGE_SIZE < count:
        rows.append([("下一页", f"draft_gate:page:{page + 1}")])
    rows.extend(
        [[("确认覆盖选定群", "draft_gate:copy_confirm")], [("取消复制", "draft_gate:copy_cancel")]]
    )
    names = {str(item["id"]): chat_title(item) for item in data["participation_groups"]}
    text = (
        f"来源群 {names[editor['chat_id']]}\n{rule_text(editor['rule'], editor['type'])}\n"
        f"将覆盖 {len(selected)} 个选定群的同类门槛（加成不变）：\n"
        + "、".join(names[key] for key in selected)
    )
    return text, rows
