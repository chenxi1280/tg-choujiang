async def picker_groups(queries, *, user_id, selected_chat_ids):
    available = await queries.admin_groups(user_id)
    selected = await queries.groups_by_chat_ids(selected_chat_ids) if selected_chat_ids else []
    by_identity = {group.id: group for group in [*available, *selected]}
    return sorted(by_identity.values(), key=lambda group: (group.title, str(group.id)))
