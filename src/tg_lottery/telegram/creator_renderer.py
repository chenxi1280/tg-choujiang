from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.admin_sessions import AdminSessionService, DraftPanelContext
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.queries import QueryService
from tg_lottery.infrastructure.db import Group
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_group_availability import (
    GroupAvailability,
    inspect_group_availability,
)

TOKEN_LIFETIME = timedelta(hours=24)
GROUP_PAGE_SIZE = 6
ButtonRows = list[list[tuple[str, str]]]


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
    ) -> None:
        target = PanelTarget(user_id, message.chat.id, message.message_id)
        keyboard = await self._keyboard(target, rows)
        await message.edit_text(text, reply_markup=keyboard)

    async def edit(
        self,
        target: PanelTarget,
        *,
        text: str,
        rows: ButtonRows,
    ) -> None:
        keyboard = await self._keyboard(target, rows)
        await self._bot.edit_message_text(
            text,
            chat_id=target.chat_id,
            message_id=target.message_id,
            reply_markup=keyboard,
        )

    async def render_groups(
        self,
        message: Message,
        *,
        user_id: int,
        selected_chat_ids: tuple[int, ...] = (),
        page: int = 0,
    ) -> None:
        await self.edit_groups(
            PanelTarget(user_id, message.chat.id, message.message_id),
            selected_chat_ids=selected_chat_ids,
            page=page,
        )

    async def edit_groups(
        self,
        target: PanelTarget,
        *,
        selected_chat_ids: tuple[int, ...] = (),
        page: int = 0,
    ) -> None:
        groups = await self._queries.admin_groups(target.user_id)
        maximum = max(0, (len(groups) - 1) // GROUP_PAGE_SIZE)
        page = min(maximum, max(0, page))
        visible = groups[page * GROUP_PAGE_SIZE : (page + 1) * GROUP_PAGE_SIZE]
        context = await self._sessions.issue_panel_context(target.user_id, target.message_id)
        rows = await self._group_buttons(
            target,
            groups=visible,
            selected_chat_ids=selected_chat_ids,
            context=context,
        )
        back = await self._keyboard(
            target,
            _group_navigation(page=page, maximum=maximum, has_groups=bool(groups)),
            context=context,
        )
        rows.extend(back.inline_keyboard)
        await self._bot.edit_message_text(
            _group_picker_text(selected_count=len(selected_chat_ids), has_groups=bool(groups)),
            chat_id=target.chat_id,
            message_id=target.message_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _group_buttons(
        self,
        target: PanelTarget,
        *,
        groups: list[Group],
        selected_chat_ids: tuple[int, ...],
        context: DraftPanelContext,
    ) -> list[list[InlineKeyboardButton]]:
        availability = [
            await inspect_group_availability(self._bot, group.chat_id, target.user_id)
            for group in groups
        ]
        parallel_counts = [
            len(await self._queries.active_campaigns_by_chat(group.chat_id)) for group in groups
        ]
        requests = tuple(
            CallbackRequest(
                "draft_toggle_group" if status.selectable else "draft_group_problem",
                payload={"chat_id": group.chat_id, "reason": status.reason},
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
        context = context or await self._sessions.issue_panel_context(
            target.user_id, target.message_id
        )
        requests = tuple(CallbackRequest(action) for row in rows for _, action in row)
        tokens = iter(await self._create_tokens(target, requests, context=context))
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=label, callback_data=f"secure:{next(tokens)}")
                    for label, _ in row
                ]
                for row in rows
            ]
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


def _group_navigation(*, page: int, maximum: int, has_groups: bool) -> ButtonRows:
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
    if not status.selectable:
        return f"⚠ {title}｜{status.status}"
    prefix = "✅ " if selected else "⬜ "
    parallel = f"｜另有 {parallel_count} 个进行中活动" if parallel_count else ""
    return prefix + title + parallel


def _group_page_rows(page: int, maximum: int) -> ButtonRows:
    if not maximum:
        return []
    return [
        [
            ("上一页", "draft_groups_prev"),
            (f"{page + 1}/{maximum + 1}", "draft_groups_refresh"),
            ("下一页", "draft_groups_next"),
        ]
    ]
