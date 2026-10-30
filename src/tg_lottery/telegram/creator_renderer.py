from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.admin_sessions import AdminSessionService, DraftPanelContext
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Group
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_group_availability import (
    GroupAvailability,
    inspect_group_availability,
)
from tg_lottery.telegram.creator_group_identity import filter_groups
from tg_lottery.telegram.creator_picker_groups import picker_groups
from tg_lottery.telegram.keyboard_layout import fit_rows, list_rows
from tg_lottery.telegram.panel_edit import edit_panel, edit_panel_at
from tg_lottery.telegram.panel_pagination import creator_page_rows, titled_pages
from tg_lottery.telegram.ui_style import style_keyboard, style_label

TOKEN_LIFETIME = timedelta(hours=24)
GROUP_PAGE_SIZE = 6
ButtonRows = list[list[tuple[str, str] | InlineKeyboardButton]]


@dataclass(frozen=True)
class PanelTarget:
    user_id: int
    chat_id: int
    message_id: int


class CreatorRenderer:
    def __init__(
        self,
        *,
        bot: Bot,
        tokens: CallbackTokenService,
        queries: QueryService,
        sessions: AdminSessionService,
    ) -> None:
        self._bot = bot
        self._tokens = tokens
        self._queries = queries
        self._sessions = sessions

    async def render(
        self,
        message: Message,
        *,
        user_id: int,
        text: str,
        rows: ButtonRows,
        parse_mode: str | None = None,
    ) -> None:
        target = PanelTarget(user_id, message.chat.id, message.message_id)
        text, rows = await self._paginate(target, text=text, rows=rows, parse_mode=parse_mode)
        keyboard = await self._keyboard(target, rows)
        await edit_panel(message, text, reply_markup=keyboard, parse_mode=parse_mode)

    async def edit(
        self,
        target: PanelTarget,
        *,
        text: str,
        rows: ButtonRows,
    ) -> None:
        text, rows = await self._paginate(target, text=text, rows=rows)
        keyboard = await self._keyboard(target, rows)
        await edit_panel_at(
            self._bot,
            text,
            chat_id=target.chat_id,
            message_id=target.message_id,
            reply_markup=keyboard,
        )

    async def _paginate(self, target, *, text, rows, parse_mode=None):
        if parse_mode is not None:
            return text, rows
        pages = titled_pages(text, title=text.split("\n", 1)[0])
        if len(pages) == 1:
            return text, rows
        session = await self._sessions.get(target.user_id)
        if session is None or not session.state.startswith("draft:"):
            raise UserFacingError("创建草稿已过期，请发送 /创建抽奖 重新打开创建入口")
        serialized = [
            [item if isinstance(item, tuple) else item.model_dump(mode="json") for item in row]
            for row in rows
        ]
        await self._sessions.set(
            target.user_id,
            session.state,
            dict(session.data)
            | {
                "content_pages": list(pages),
                "content_rows": serialized,
            },
        )
        return pages[0], creator_page_rows(rows, page=0, total=len(pages))

    async def page(self, query, grant):
        session = await self._sessions.get(query.from_user.id)
        if session is None or not session.state.startswith("draft:"):
            raise UserFacingError("创建草稿已过期，请发送 /创建抽奖 重新打开创建入口")
        pages = session.data.get("content_pages")
        content_rows = session.data.get("content_rows")
        if not pages or not isinstance(content_rows, list):
            raise UserFacingError("内容页已失效，请重新打开当前草稿页面")
        page = int(grant.action.removeprefix("draft_content_page:"))
        if page < 0 or page >= len(pages):
            raise UserFacingError("内容页已失效，请重新打开当前草稿页面")
        rows = [
            [
                InlineKeyboardButton.model_validate(item) if isinstance(item, dict) else tuple(item)
                for item in row
            ]
            for row in content_rows
        ]
        target = PanelTarget(query.from_user.id, query.message.chat.id, query.message.message_id)
        keyboard = await self._keyboard(
            target, creator_page_rows(rows, page=page, total=len(pages))
        )
        await edit_panel(query.message, pages[page], reply_markup=keyboard)

    async def keyboard_for(
        self,
        target: PanelTarget,
        rows: ButtonRows,
    ) -> InlineKeyboardMarkup:
        """Build buttons scoped to the id of an already sent panel."""
        return await self._keyboard(target, rows)

    async def restore_list(self, target, *, state, data, notice):
        await self._sessions.set(target.user_id, state, data)
        if state == "draft:groups":
            await self.edit_groups(
                target,
                selected_chat_ids=tuple(data.get("participation_group_ids") or []),
                page=int(data.get("group_page", 0)),
                search=str(data.get("group_search", "")),
                notice=notice,
            )
            return
        from tg_lottery.telegram.creator_admission_picker import admission_view

        groups = await self._queries.admin_groups(target.user_id)
        updated, text, rows = admission_view(groups, data)
        await self._sessions.set(target.user_id, "draft:admission_picker", updated)
        await self.edit(target, text=notice + "\n\n" + text, rows=rows)

    async def render_groups(
        self,
        message: Message,
        *,
        user_id: int,
        selected_chat_ids: tuple[int, ...] = (),
        page: int = 0,
        search: str = "",
        verified_availability: dict[int, GroupAvailability] | None = None,
    ) -> None:
        await self.edit_groups(
            PanelTarget(user_id, message.chat.id, message.message_id),
            selected_chat_ids=selected_chat_ids,
            page=page,
            search=search,
            verified_availability=verified_availability,
        )

    async def edit_groups(
        self,
        target: PanelTarget,
        *,
        selected_chat_ids: tuple[int, ...] = (),
        page: int = 0,
        search: str = "",
        verified_availability: dict[int, GroupAvailability] | None = None,
        notice: str = "",
    ) -> None:
        groups, selected_chat_ids = await self._picker_group_state(
            target.user_id, selected_chat_ids=selected_chat_ids, search=search
        )
        maximum = max(0, (len(groups) - 1) // GROUP_PAGE_SIZE)
        requested_page = page
        page = min(maximum, max(0, page))
        await self._store_group_page(target.user_id, page)
        visible = groups[page * GROUP_PAGE_SIZE : (page + 1) * GROUP_PAGE_SIZE]
        context = await self._sessions.issue_panel_context(target.user_id, target.message_id)
        rows = await self._group_buttons(
            target,
            groups=visible,
            selected_chat_ids=selected_chat_ids,
            context=context,
            verified_availability=verified_availability,
        )
        back = await self._keyboard(
            target,
            _group_navigation(
                page=page, maximum=maximum, has_groups=bool(groups), searching=bool(search)
            ),
            context=context,
        )
        rows = list_rows(rows, start=page * GROUP_PAGE_SIZE + 1)
        rows.extend(back.inline_keyboard)
        await edit_panel_at(
            self._bot,
            (notice + "\n\n" if notice else "")
            + _group_picker_text(selected_count=len(selected_chat_ids), has_groups=bool(groups))
            + ("\n页码已随当前群列表调整。" if page != requested_page else "")
            + ("\n搜索：" + search if search else "")
            + "\n\n"
            + "\n".join(
                f"{page * GROUP_PAGE_SIZE + i}. {g.title}（{g.chat_id}）"
                for i, g in enumerate(visible, start=1)
            ),
            chat_id=target.chat_id,
            message_id=target.message_id,
            reply_markup=style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows)),
        )

    async def _picker_group_state(self, user_id, *, selected_chat_ids, search):
        groups = await picker_groups(
            self._queries, user_id=user_id, selected_chat_ids=selected_chat_ids
        )
        selected = set(selected_chat_ids)
        for group in groups:
            if group.migrated_from_chat_id in selected:
                selected = (selected - {group.migrated_from_chat_id}) | {group.chat_id}
        return filter_groups(groups, search), tuple(selected)

    async def _store_group_page(self, user_id, page):
        session = await self._sessions.get(user_id)
        if session is None:
            raise ValueError("admin draft session expired")
        await self._sessions.set(user_id, session.state, dict(session.data) | {"group_page": page})

    async def _group_availability_rows(self, groups, *, user_id, verified):
        return await asyncio.gather(
            *(
                self._group_availability(group.chat_id, user_id=user_id, verified=verified)
                for group in groups
            )
        )

    async def _group_availability(self, chat_id, *, user_id, verified):
        if verified and chat_id in verified:
            return verified[chat_id]
        return await inspect_group_availability(self._bot, chat_id, user_id)

    async def _group_buttons(
        self,
        target: PanelTarget,
        *,
        groups: list[Group],
        selected_chat_ids: tuple[int, ...],
        context: DraftPanelContext,
        verified_availability: dict[int, GroupAvailability] | None = None,
    ) -> list[list[InlineKeyboardButton]]:
        availability, counts = await asyncio.gather(
            self._group_availability_rows(
                groups, user_id=target.user_id, verified=verified_availability
            ),
            self._queries.active_campaign_counts_by_chats(tuple(group.chat_id for group in groups)),
        )
        parallel_counts = [counts.get(group.chat_id, 0) for group in groups]
        requests = tuple(
            CallbackRequest(
                "draft_toggle_group"
                if status.selectable or group.chat_id in selected_chat_ids
                else "draft_group_problem",
                payload={
                    "chat_id": group.chat_id,
                    "group_id": str(group.id),
                    "reason": status.reason,
                },
            )
            for group, status in zip(groups, availability, strict=True)
        )
        tokens = await self._create_tokens(target, requests, context=context)
        return [
            [
                InlineKeyboardButton(
                    text=_group_button_text(
                        group,
                        status,
                        group.chat_id in selected_chat_ids,
                        parallel_count=parallel_count,
                    ),
                    callback_data=f"secure:{token}",
                ),
            ]
            for group, status, token, parallel_count in zip(
                groups, availability, tokens, parallel_counts, strict=True
            )
        ]

    async def _keyboard(
        self,
        target: PanelTarget,
        rows: ButtonRows,
        *,
        context: DraftPanelContext | None = None,
    ) -> InlineKeyboardMarkup:
        rows = fit_rows(rows)
        context = context or await self._sessions.issue_panel_context(
            target.user_id, target.message_id
        )
        requests = tuple(
            CallbackRequest(item[1]) for row in rows for item in row if isinstance(item, tuple)
        )
        tokens = iter(await self._create_tokens(target, requests, context=context))
        return style_keyboard(
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        item
                        if isinstance(item, InlineKeyboardButton)
                        else InlineKeyboardButton(
                            text=style_label(item[0], action=item[1]),
                            callback_data=f"secure:{next(tokens)}",
                        )
                        for item in row
                    ]
                    for row in rows
                ]
            )
        )

    async def _create_tokens(
        self,
        target: PanelTarget,
        requests: tuple[CallbackRequest, ...],
        *,
        context: DraftPanelContext | None = None,
    ) -> tuple[str, ...]:
        context = context or await self._sessions.issue_panel_context(
            target.user_id, target.message_id
        )
        payload = context.callback_payload()
        scoped_requests = tuple(
            CallbackRequest(
                request.action,
                campaign_id=request.campaign_id,
                payload={**(request.payload or {}), **payload},
            )
            for request in requests
        )
        return await self._tokens.create_many(
            owner_user_id=target.user_id,
            chat_id=target.chat_id,
            requests=scoped_requests,
            expires_in=TOKEN_LIFETIME,
        )


def _group_navigation(
    *, page: int, maximum: int, has_groups: bool, searching: bool = False
) -> ButtonRows:
    selection = (
        [
            [("全选本页", "draft_groups_select_page"), ("清空本页", "draft_groups_clear_page")],
        ]
        if has_groups
        else []
    )
    return [
        *_group_page_rows(page, maximum),
        *selection,
        *(
            [[("搜索群名/ID", "draft_groups_search"), ("清除搜索", "draft_groups_search_clear")]]
            if has_groups or searching
            else []
        ),
        [("🔄 刷新群列表", "draft_groups_refresh")],
        *([[("✅ 完成选择", "draft_groups_done")]] if has_groups else []),
        [("↩ 返回上一层", "draft_back")],
    ]


def _group_picker_text(*, selected_count: int, has_groups: bool) -> str:
    title = f"👥 参与活动的群（已选 {selected_count}）\n\n"
    if not has_groups:
        return (
            title + "当前没有可管理的群。\n"
            "你可以先返回预览设置标题和奖品。\n"
            "发布前，请将机器人加入目标群，并确保你和机器人都是该群管理员，再点刷新。"
        )
    return (
        title + "参与者必须同时加入全部已选群；每个群都会收到活动卡。\n"
        "若群不在列表，请确认你和机器人都是该群管理员，再点刷新。"
    )


def _group_button_text(
    group: Group,
    status: GroupAvailability,
    selected: bool,
    *,
    parallel_count: int,
) -> str:
    title = chat_title({"id": group.chat_id, "title": group.title})
    title = title[:28] + f" · {str(group.chat_id)[-6:]}"
    if not status.selectable:
        return f"✅ 已选 · {title}｜点此移除" if selected else f"⚠ {title}｜{status.status}"
    prefix = "✅ " if selected else "⬜ "
    parallel = f"｜另有 {parallel_count} 个进行中活动" if parallel_count else ""
    return prefix + title + parallel


def _group_page_rows(page: int, maximum: int) -> ButtonRows:
    rows = []
    if page:
        rows.append(("上一页", "draft_groups_prev"))
    if page < maximum:
        rows.append(("下一页", "draft_groups_next"))
    return [rows] if rows else []
