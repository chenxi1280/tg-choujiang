from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_message_context import callback_message
from tg_lottery.telegram.creator_renderer import PanelTarget
from tg_lottery.telegram.creator_resume_views import resumed_view
from tg_lottery.telegram.home_views import home_keyboard, home_text


async def restore_saved_panel(query, *, sessions, renderer, state, data, notice):
    updated, restored_state, (text, rows) = resumed_view(data, state=state, module=False)
    await sessions.set(query.from_user.id, restored_state, updated)
    await renderer.edit(
        PanelTarget(query.from_user.id, query.message.chat.id, int(updated["panel_message_id"])),
        text=notice + "\n\n" + text,
        rows=rows,
    )


async def recover_gate_panel(query, *, sessions, renderer, session, data):
    if session.state != "draft:behavior_admission":
        return False
    await restore_saved_panel(
        query,
        sessions=sessions,
        renderer=renderer,
        state=session.state,
        data=data,
        notice="旧按钮操作未执行，已恢复当前门槛编辑页；已填写内容保留。",
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
        await message.edit_text(home_text(session.data), reply_markup=home_keyboard(session.data))
        return
    await restore_saved_panel(
        query,
        sessions=sessions,
        renderer=renderer,
        state=session.state,
        data=dict(session.data),
        notice="本次修改已记录，已恢复当前草稿显示",
    )
