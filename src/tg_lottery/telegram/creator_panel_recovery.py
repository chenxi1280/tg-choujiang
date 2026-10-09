from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_message_context import callback_message
from tg_lottery.telegram.creator_prize_confirmation import CONFIRMATION_STATES
from tg_lottery.telegram.creator_renderer import PanelTarget
from tg_lottery.telegram.creator_resume_views import LIST_STATES, has_field_input, resumed_view
from tg_lottery.telegram.home_views import home_keyboard, home_text
from tg_lottery.telegram.panel_edit import edit_panel


async def restore_saved_panel(query, *, sessions, renderer, state, data, notice):
    target = PanelTarget(query.from_user.id, query.message.chat.id, int(data["panel_message_id"]))
    if state in LIST_STATES:
        await renderer.restore_list(target, state=state, data=data, notice=notice)
        return
    updated, restored_state, (text, rows) = resumed_view(data, state=state, module=False)
    await sessions.set(query.from_user.id, restored_state, updated)
    await renderer.edit(
        target,
        text=notice + "\n\n" + text,
        rows=rows,
    )


async def recover_editing_panel(query, *, sessions, renderer, session, data):
    gate = session.state == "draft:behavior_admission"
    bonus = session.state in {"draft:idle", "draft:bonus_input"} and (
        data.get("bonus_working_copy") or data.get("bonus_copy")
    )
    field = has_field_input(session.state, data)
    confirmation = session.state in CONFIRMATION_STATES | {
        "draft:publish_confirm",
        "draft:group_remove_confirm",
    }
    if not any((gate, bonus, field, confirmation, session.state in LIST_STATES)):
        return False
    await restore_saved_panel(
        query,
        sessions=sessions,
        renderer=renderer,
        state=session.state,
        data=data,
        notice=(
            "旧按钮操作未执行，已恢复当前门槛编辑页；已填写内容保留。"
            if gate
            else (
                "旧按钮操作未执行，已恢复当前加成编辑步骤；已填写内容保留。"
                if bonus
                else "旧按钮操作未执行，已恢复当前设置步骤；已填写内容保留。"
            )
        ),
    )
    return True


async def restore_applied_panel(query, *, sessions, renderer):
    message = callback_message(query)
    if message.chat.type != "private" or message.chat.id != query.from_user.id:
        raise UserFacingError("请在本人私聊恢复创建草稿")
    session = await sessions.get(query.from_user.id)
    if session is None or not session.state.startswith("draft:"):
        raise UserFacingError("创建草稿已过期，请重新打开创建入口")
    if session.state == "draft:suspended":
        await edit_panel(message, home_text(session.data), reply_markup=home_keyboard(session.data))
        return
    await restore_saved_panel(
        query,
        sessions=sessions,
        renderer=renderer,
        state=session.state,
        data=dict(session.data),
        notice="本次修改已记录，已恢复当前草稿显示",
    )
