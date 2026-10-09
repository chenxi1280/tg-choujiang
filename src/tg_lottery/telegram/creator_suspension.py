from tg_lottery.telegram import creator_group_bonus as bonus
from tg_lottery.telegram.creator_behavior_editor import dirty as gate_dirty
from tg_lottery.telegram.creator_message_context import callback_message
from tg_lottery.telegram.creator_renderer import PanelTarget
from tg_lottery.telegram.creator_resume_views import LIST_STATES, has_field_input, resumed_view


async def suspend_draft(sessions, user_id):
    session = await sessions.get(user_id)
    if session is None or not session.state.startswith("draft:"):
        return
    if session.state in {"draft:suspended", "draft:publishing"}:
        return
    await sessions.set(
        user_id,
        "draft:suspended",
        dict(session.data)
        | {
            "suspended_state": session.state,
        },
    )


def resume_view(data):
    rows = [[("返回当前模块", "draft_resume_module")]]
    state = data.get("suspended_state", "draft:idle")
    if has_field_input(state, data):
        rows.insert(0, [("继续原字段输入", "draft_resume_input")])
    if bonus.working_copy_dirty(data):
        rows.insert(0, [("继续编辑未保存加成", "draft_bonus_continue_editing")])
        rows.append([("放弃加成修改", "draft_bonus_group_cancel")])
    if gate_dirty(data):
        rows.insert(0, [("继续编辑未保存门槛", "draft_gate")])
        rows.append([("放弃门槛修改", "draft_gate:discard")])
    return "📝 草稿已保留\n\n请选择继续原字段输入或回到当前模块。未保存编辑不会自动提交。", rows


class CreatorSuspension:
    def __init__(self, *, sessions, renderer):
        self._sessions = sessions
        self._renderer = renderer

    async def resume(self, query, grant):
        session = await self._sessions.get(query.from_user.id)
        data = dict(session.data)
        state = data.get("suspended_state", "draft:idle")
        data = {key: value for key, value in data.items() if key != "suspended_state"}
        if state in LIST_STATES or (
            state == "draft:admission_search" and grant.action == "draft_resume_module"
        ):
            message = callback_message(query)
            await self._renderer.restore_list(
                PanelTarget(query.from_user.id, message.chat.id, message.message_id),
                state="draft:admission_picker" if state == "draft:admission_search" else state,
                data=data | {"panel_message_id": message.message_id},
                notice="已恢复当前列表；搜索和已选项保留。",
            )
            return
        groups_return = state == "draft:group_search" and grant.action == "draft_resume_module"
        data, state, (text, rows) = resumed_view(
            data, state=state, module=grant.action == "draft_resume_module"
        )
        await self._sessions.set(query.from_user.id, state, data)
        if groups_return:
            await self._renderer.render_groups(
                callback_message(query),
                user_id=query.from_user.id,
                selected_chat_ids=tuple(data.get("participation_group_ids") or []),
                page=int(data.get("group_page", 0)),
                search=str(data.get("group_search", "")),
            )
            return
        await self._renderer.render(
            callback_message(query), user_id=query.from_user.id, text=text, rows=rows
        )
