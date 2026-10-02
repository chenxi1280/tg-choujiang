from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_group_selection import selected_chat_ids
from tg_lottery.telegram.creator_participation import message_chat_refs, participation_add_text
from tg_lottery.telegram.creator_renderer import GROUP_PAGE_SIZE, PanelTarget
from tg_lottery.telegram.creator_required_chats_data import required_draft_data, with_required_chats
from tg_lottery.telegram.required_chats import resolve_required_chat

PREFIX = "draft_admission:"


async def add_admission_chats(bot, data: dict[str, Any], *, refs, user_id: int) -> dict[str, Any]:
    if not refs:
        raise UserFacingError("请至少填写一个群或频道")
    chats = list(data.get("required_chats") or [])
    existing = {int(chat["id"]) for chat in chats}
    participating = set(selected_chat_ids(data))
    for ref in refs:
        chat = await resolve_required_chat(bot, ref, admin_user_id=user_id)
        chat_id = int(chat["id"])
        if chat_id in participating:
            raise UserFacingError("该目标已经是参与活动的群，不能同时作为准入条件")
        if chat_id in existing:
            raise UserFacingError("该群或频道已经在参与条件中")
        chats.append(chat)
        existing.add(chat_id)
    return with_required_chats(data, chats)


class CreatorAdmissionPicker:
    def __init__(self, *, bot, sessions, queries, renderer) -> None:
        self._bot = bot
        self._sessions = sessions
        self._queries = queries
        self._renderer = renderer

    async def show(self, query, _) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        updated = data | {
            "panel_message_id": query.message.message_id,
            "guided": False,
            "admission_page": 0,
            "admission_search": "",
        }
        await self._render(query.from_user.id, query.message.chat.id, updated)

    async def handle(self, query, grant) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        action = grant.action.removeprefix(PREFIX)
        if action == "search":
            await self._search_prompt(query, data)
            return
        if action.startswith("select:"):
            data = await self._toggle(data, int(action.split(":")[1]), query.from_user.id)
        if action == "clear_search":
            data = data | {"admission_search": "", "admission_page": 0}
        if action in {"prev", "next"}:
            delta = -1 if action == "prev" else 1
            data = data | {"admission_page": int(data.get("admission_page", 0)) + delta}
        if not action.startswith("select:") and action not in {
            "refresh",
            "clear_search",
            "prev",
            "next",
        }:
            raise UserFacingError("未知的准入列表操作，请重新打开准入条件")
        await self._render(query.from_user.id, query.message.chat.id, data)

    async def receive(self, message, data: dict[str, Any], *, searching: bool = False) -> None:
        if searching and not getattr(message, "forward_origin", None):
            if not message.text:
                raise UserFacingError("请发送群名称关键词或转发来源可见的群/频道消息")
            updated = data | {"admission_search": (message.text or "").strip(), "admission_page": 0}
        else:
            updated = await add_admission_chats(
                self._bot, data, refs=message_chat_refs(message), user_id=message.from_user.id
            )
        await self._render(message.from_user.id, message.chat.id, updated)

    async def _toggle(self, data: dict[str, Any], chat_id: int, user_id: int) -> dict[str, Any]:
        chats = data.get("required_chats") or []
        if any(int(chat["id"]) == chat_id for chat in chats):
            return with_required_chats(data, [chat for chat in chats if int(chat["id"]) != chat_id])
        return await add_admission_chats(self._bot, data, refs=[chat_id], user_id=user_id)

    async def _search_prompt(self, query, data: dict[str, Any]) -> None:
        await self._sessions.set(query.from_user.id, "draft:admission_search", data)
        await self._renderer.render(
            query.message,
            user_id=query.from_user.id,
            text="🔍 搜索准入群/频道\n\n请发送名称关键词；也可直接转发来源可见的群或频道消息。",
            rows=[[("↩ 返回群列表", PREFIX + "refresh")]],
        )

    async def _render(self, user_id: int, chat_id: int, data: dict[str, Any]) -> None:
        groups = await self._queries.admin_groups(user_id)
        candidates = admission_candidates(groups, data)
        maximum = max(0, (len(candidates) - 1) // GROUP_PAGE_SIZE)
        page = min(maximum, max(0, int(data.get("admission_page", 0))))
        updated = data | {"admission_page": page}
        visible = candidates[page * GROUP_PAGE_SIZE : (page + 1) * GROUP_PAGE_SIZE]
        rows = admission_rows(visible, updated, maximum=maximum)
        search = str(data.get("admission_search") or "")
        text = (
            participation_add_text()
            + f"\n\n共 {len(candidates)} 项 · 第 {page + 1}/{maximum + 1} 页"
        )
        if search:
            text += f"\n搜索：{search}"
        if not candidates:
            text += "\n没有匹配的群/频道，可清除搜索、刷新列表或转发来源可见的消息。"
        await self._sessions.set(user_id, "draft:admission_picker", updated)
        await self._renderer.edit(
            PanelTarget(user_id, chat_id, int(data["panel_message_id"])), text=text, rows=rows
        )


def admission_candidates(groups, data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = {group.chat_id: {"id": group.chat_id, "title": group.title} for group in groups}
    candidates.update({int(chat["id"]): chat for chat in data.get("required_chats") or []})
    participating = set(selected_chat_ids(data))
    keyword = str(data.get("admission_search") or "").casefold()
    return sorted(
        [
            chat
            for key, chat in candidates.items()
            if key not in participating and keyword in str(chat["title"]).casefold()
        ],
        key=lambda chat: (str(chat["title"]).casefold(), int(chat["id"])),
    )


def admission_rows(visible, data: dict[str, Any], *, maximum: int):
    selected = {int(chat["id"]) for chat in data.get("required_chats") or []}
    rows = [
        [
            (
                f"{'✅' if int(chat['id']) in selected else '⬜'} {chat['title']}",
                f"{PREFIX}select:{chat['id']}",
            )
        ]
        for chat in visible
    ]
    navigation = []
    page = int(data.get("admission_page", 0))
    if page > 0:
        navigation.append(("← 上一页", PREFIX + "prev"))
    if page < maximum:
        navigation.append(("下一页 →", PREFIX + "next"))
    if navigation:
        rows.append(navigation)
    rows.append([("🔍 搜索", PREFIX + "search"), ("🔄 刷新", PREFIX + "refresh")])
    if data.get("admission_search"):
        rows.append([("清除搜索", PREFIX + "clear_search")])
    rows.append([("↩ 返回条件列表", "draft_participation")])
    return rows
