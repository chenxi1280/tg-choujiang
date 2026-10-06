from tg_lottery.application.request_context import current_update_id
from tg_lottery.telegram.creator_message_context import panel_target
from tg_lottery.telegram.creator_resume_views import resumed_view

MUTATION_SOURCE_KEY = "mutation_source_update_id"


async def recover_applied_input(renderer, message, session) -> bool:
    source = current_update_id()
    applied = session.data.get(MUTATION_SOURCE_KEY)
    if source is None or applied is None or source > int(applied):
        return False
    if source < int(applied) or session.state == "draft:suspended":
        await message.answer("原输入所属页面已更新，本次没有按新字段重复提交。请使用当前页面继续。")
        return True
    data, _, (text, rows) = resumed_view(dict(session.data), state=session.state, module=False)
    await renderer.edit(
        panel_target(message, data), text="已恢复当前草稿显示\n\n" + text, rows=rows
    )
    return True
