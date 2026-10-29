from tg_lottery.telegram import creator_behavior_views, creator_group_bonus
from tg_lottery.telegram.creator_bonus_copy import copy_view
from tg_lottery.telegram.creator_cover import cover_prompt_text, cover_rows
from tg_lottery.telegram.creator_current_view import current_draft_view
from tg_lottery.telegram.creator_participation import participation_rows, participation_text
from tg_lottery.telegram.creator_prize_confirmation import CONFIRMATION_STATES, confirmation_view

INPUT_LABELS = {
    "draft:title": "活动标题",
    "draft:prize_name": "奖品名称",
    "draft:prize_count": "中奖人数（正整数人）",
    "draft:prize_delete": "要删除的奖品编号",
    "draft:start": "开始时间（北京时间）",
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
    "draft:folder_link": "Telegram 组合分享链接（https://t.me/addlist/…）",
    "draft:chat_threshold": "准入群的发言条数",
}
LIST_STATES = frozenset({"draft:groups", "draft:admission_picker"})


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
    if state in {"draft:bonus_input", "draft:idle"} and (
        data.get("bonus_working_copy") or data.get("bonus_copy")
    ):
        return _resumed_bonus(data, state=state, module=module)
    if not module:
        confirmation = _confirmation_step(data, state)
        if confirmation is not None:
            return data, state, confirmation
    return _resumed_field(data, state=state, module=module)


def _confirmation_step(data, state):
    if state in CONFIRMATION_STATES:
        return confirmation_view(data, state)
    if state == "draft:group_remove_confirm":
        from tg_lottery.telegram.creator_group_picker import removal_confirmation_view

        return removal_confirmation_view(data)
    if state == "draft:publish_confirm":
        from tg_lottery.telegram.creator_publication import confirmation_view as publish_view

        return publish_view(data)
    return None


def _resumed_field(data, *, state, module):
    if module or state not in INPUT_LABELS:
        return data, "draft:idle", current_draft_view(data)
    if state == "draft:participation":
        return data, state, (participation_text(data), participation_rows(data))
    if state == "draft:cover":
        has_cover = bool(data.get("cover_file_id"))
        return (
            data,
            state,
            (cover_prompt_text(has_cover=has_cover), cover_rows(has_cover=has_cover)),
        )
    text = "⌨️ 已恢复输入：" + INPUT_LABELS[state] + "\n请发送内容；发送前可取消该字段输入。"
    if state == "draft:prize_count":
        text += "\n🎁 本次奖品：" + str(data["pending_prize_name"]) + "（尚未加入奖品清单）"
    return data, state, (text, _input_rows(state))


def _input_rows(state):
    if state in {"draft:prize_count", "draft:prize_name"}:
        return [[("重新输入名称", "draft_prize_rename")], [("取消新增奖品", "draft_prize_cancel")]]
    if state == "draft:prize_delete":
        return [[("返回奖品与领奖", "draft_prizes")]]
    if state in {"draft:folder_link", "draft:chat_threshold"}:
        return [[("返回条件列表", "draft_participation")]]
    if state == "draft:group_search":
        return [[("返回群列表", "draft_group")]]
    if state == "draft:admission_search":
        return [[("返回群列表", "draft_admission:refresh")]]
    return [[("取消本次输入", "draft_back")]]


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
    if data.get("bonus_copy"):
        return updated, "draft:idle", copy_view(updated)
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
