from tg_lottery.telegram import creator_behavior_views, creator_group_bonus
from tg_lottery.telegram.creator_cover import cover_prompt_text, cover_rows
from tg_lottery.telegram.creator_current_view import current_draft_view

INPUT_LABELS = {
    "draft:title": "活动标题",
    "draft:prize_name": "奖品名称",
    "draft:prize_count": "中奖人数（正整数人）",
    "draft:draw": "开奖时间（北京时间）",
    "draft:claim": "领奖说明",
    "draft:cover": "封面图片",
    "draft:regions": "允许地区",
    "draft:description": "抽奖说明",
    "draft:issuer": "发奖人员",
    "draft:group_search": "参与群搜索",
    "draft:admission_search": "额外条件搜索",
    "draft:participation_delete": "要移除的额外条件编号",
    "draft:participation": "额外条件链接或来源消息",
}


def has_field_input(state, data):
    if state in INPUT_LABELS:
        return True
    if state == "draft:bonus_input":
        return bool(data.get("bonus_input_field"))
    if state == "draft:behavior_admission":
        return bool((data.get("admission_editor") or {}).get("field"))
    return False


def resumed_view(data, *, state, module):
    if state == "draft:behavior_admission":
        return _resumed_gate(data, module=module)
    if state in {"draft:bonus_input", "draft:idle"} and data.get("bonus_working_copy"):
        return _resumed_bonus(data, state=state, module=module)
    if module or state not in INPUT_LABELS:
        return data, "draft:idle", current_draft_view(data)
    if state == "draft:cover":
        has_cover = bool(data.get("cover_file_id"))
        return (
            data,
            state,
            (cover_prompt_text(has_cover=has_cover), cover_rows(has_cover=has_cover)),
        )
    text = "已恢复输入：" + INPUT_LABELS[state] + "\n请发送内容；发送前可取消该字段输入。"
    if state == "draft:prize_count":
        text += "\n本次奖品：" + str(data["pending_prize_name"]) + "（尚未加入奖品清单）"
    rows = [[("取消本次输入", "draft_back")]]
    if state in {"draft:prize_count", "draft:prize_name"}:
        rows = [[("重新输入名称", "draft_prize_rename")], [("取消新增奖品", "draft_prize_cancel")]]
    return data, state, (text, rows)


def _resumed_gate(data, *, module):
    from tg_lottery.telegram.creator_retention_revision import (
        retention_conflict_view,
        synchronize_retention,
    )

    editor = dict(data["admission_editor"])
    updated = data | {"admission_editor": editor | {"field": None}} if module else data
    updated = synchronize_retention(updated)
    view = (
        retention_conflict_view(updated)
        if updated["admission_editor"].get("retention_conflict")
        else creator_behavior_views.view(updated)
    )
    return updated, "draft:behavior_admission", view


def _resumed_bonus(data, *, state, module):
    updated = {key: value for key, value in data.items() if key != "bonus_input_field"}
    if module or state == "draft:idle":
        return (
            updated,
            "draft:idle",
            creator_group_bonus.editor_view(updated, updated["bonus_working_copy"]),
        )
    return (
        data,
        state,
        (creator_group_bonus.input_prompt(data), [[("取消输入", "draft_bonus_input_cancel")]]),
    )
