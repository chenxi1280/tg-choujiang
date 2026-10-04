from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Group
from tg_lottery.telegram.creator_group_availability import (
    GroupAvailability,
    inspect_group_availability,
)
from tg_lottery.telegram.creator_group_selection import selected_chat_ids, toggle_group
from tg_lottery.telegram.creator_renderer import GROUP_PAGE_SIZE, CreatorRenderer
from tg_lottery.telegram.creator_views import dashboard_rows, preview_text


class CreatorGroupPicker:
    def __init__(
        self,
        *,
        bot: Bot,
        sessions: AdminSessionService,
        queries: QueryService,
        renderer: CreatorRenderer,
    ) -> None:
        self._bot = bot
        self._sessions = sessions
        self._queries = queries
        self._renderer = renderer

    async def show(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        updated = data | {"panel_message_id": _message(query).message_id, "guided": False}
        await self._sessions.set(query.from_user.id, "draft:idle", updated)
        await self._render_groups(query, updated)

    async def toggle(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        chat_id = int(grant.payload["chat_id"])
        data = await self._required_data(query.from_user.id)
        group = await self._required_group(chat_id)
        selected = chat_id in selected_chat_ids(data)
        if selected and _group_has_rules(data, chat_id):
            await self._confirm_removal(query, data, [group])
            return
        if selected:
            await self._save_and_render(query, toggle_group(data, group))
            return
        availability = await self._availability(chat_id, query.from_user.id)
        if not availability.selectable:
            raise UserFacingError(availability.reason)
        await self._save_and_render(query, toggle_group(data, group))

    async def select_page(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        selected = set(selected_chat_ids(data))
        for group in await self._visible_groups(query.from_user.id, data):
            availability = await self._availability(group.chat_id, query.from_user.id)
            if availability.selectable and group.chat_id not in selected:
                data = toggle_group(data, group)
                selected.add(group.chat_id)
        await self._save_and_render(query, data)

    async def clear_page(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        selected = set(selected_chat_ids(data))
        groups = [
            group
            for group in await self._visible_groups(query.from_user.id, data)
            if group.chat_id in selected
        ]
        configured = [group for group in groups if _group_has_rules(data, group.chat_id)]
        if configured:
            await self._confirm_removal(query, data, groups)
            return
        await self._save_and_render(query, _remove_groups(data, groups))

    async def confirm_remove(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        chat_ids = [int(value) for value in data.get("pending_group_removal_ids") or []]
        groups = [await self._required_group(chat_id) for chat_id in chat_ids]
        updated = _clear_pending_removal(_remove_groups(data, groups))
        await self._save_and_render(query, updated)

    async def cancel_remove(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = _clear_pending_removal(await self._required_data(query.from_user.id))
        await self._save_and_render(query, data)

    async def page(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        delta = -1 if grant.action.endswith("prev") else 1 if grant.action.endswith("next") else 0
        updated = data | {"group_page": max(0, int(data.get("group_page", 0)) + delta)}
        await self._save_and_render(query, updated)

    async def done(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        if not selected_chat_ids(data):
            raise UserFacingError("请至少选择一个参与活动的群")
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await self._renderer.render(
            _message(query),
            user_id=query.from_user.id,
            text=preview_text(data),
            rows=dashboard_rows(data),
        )

    async def problem(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._required_data(query.from_user.id)
        reason = str(grant.payload.get("reason") or "该群当前不可用，请重新检测")
        rows = [
            [("🔄 重新检测", "draft_groups_refresh")],
            [("↩ 返回群列表", "draft_group")],
        ]
        await self._renderer.render(
            _message(query),
            user_id=query.from_user.id,
            text=f"⚠️ 该群暂不可选\n\n{reason}",
            rows=rows,
        )

    async def _confirm_removal(
        self, query: CallbackQuery, data: dict[str, Any], groups: list[Group]
    ) -> None:
        chat_ids = [group.chat_id for group in groups]
        updated = data | {"pending_group_removal_ids": chat_ids}
        configured = [group.title for group in groups if _group_has_rules(data, group.chat_id)]
        text = (
            f"移除 {len(groups)} 个参与群？\n\n"
            f"其中已配置加成的群：{'、'.join(configured)}。确认后将删除这些群的草稿规则。"
        )
        rows = [
            [("确认移除并删除设置", "draft_group_remove_confirm")],
            [("取消", "draft_group_remove_cancel")],
        ]
        await self._sessions.set(query.from_user.id, "draft:idle", updated)
        await self._renderer.render(
            _message(query), user_id=query.from_user.id, text=text, rows=rows
        )

    async def _save_and_render(self, query: CallbackQuery, data: dict[str, Any]) -> None:
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await self._render_groups(query, data)

    async def _render_groups(self, query: CallbackQuery, data: dict[str, Any]) -> None:
        await self._renderer.render_groups(
            _message(query),
            user_id=query.from_user.id,
            selected_chat_ids=selected_chat_ids(data),
            page=int(data.get("group_page", 0)),
        )

    async def _visible_groups(self, user_id: int, data: dict[str, Any]) -> list[Group]:
        groups = await self._queries.admin_groups(user_id)
        page = int(data.get("group_page", 0))
        return groups[page * GROUP_PAGE_SIZE : (page + 1) * GROUP_PAGE_SIZE]

    async def _availability(self, chat_id: int, user_id: int) -> GroupAvailability:
        return await inspect_group_availability(self._bot, chat_id, user_id)

    async def _required_group(self, chat_id: int) -> Group:
        group = await self._queries.group_by_chat_id(chat_id)
        if group is None:
            raise UserFacingError("活动群不存在")
        return group

    async def _required_data(self, user_id: int) -> dict[str, Any]:
        session = await self._sessions.get(user_id)
        if session is None or not session.state.startswith("draft:"):
            raise UserFacingError("创建草稿已过期，请发送 /new 重新创建")
        return dict(session.data)


def _remove_groups(data: dict[str, Any], groups: list[Group]) -> dict[str, Any]:
    result = dict(data)
    selected = set(selected_chat_ids(result))
    for group in groups:
        if group.chat_id in selected:
            result = toggle_group(result, group)
            selected.remove(group.chat_id)
    return result


def _group_has_rules(data: dict[str, Any], chat_id: int) -> bool:
    key = str(chat_id)
    return key in (data.get("admission") or {}).get("groups", {}) or any(
        key in (data.get(field) or {})
        for field in ("group_invite_rules", "group_activity_rules", "base_activity_rules")
    )


def _clear_pending_removal(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    result.pop("pending_group_removal_ids", None)
    return result


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message
