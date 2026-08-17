from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.admin_sessions import AdminSessionService, DraftPanelContext
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.queries import QueryService

TOKEN_LIFETIME = timedelta(hours=24)
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
        primary_chat_id: int | None = None,
    ) -> None:
        await self.edit_groups(
            PanelTarget(user_id, message.chat.id, message.message_id),
            selected_chat_ids=selected_chat_ids,
            primary_chat_id=primary_chat_id,
        )

    async def edit_groups(
        self,
        target: PanelTarget,
        *,
        selected_chat_ids: tuple[int, ...] = (),
        primary_chat_id: int | None = None,
    ) -> None:
        groups = await self._queries.admin_groups(target.user_id)
        context = await self._sessions.issue_panel_context(target.user_id, target.message_id)
        requests = tuple(
            request
            for group in groups
            for request in (
                CallbackRequest("draft_toggle_group", payload={"chat_id": group.chat_id}),
                CallbackRequest("draft_primary_group", payload={"chat_id": group.chat_id}),
            )
        )
        tokens = await self._create_tokens(target, requests, context=context)
        token_pairs = zip(tokens[::2], tokens[1::2], strict=True)
        rows = [
            [
                InlineKeyboardButton(
                    text=("✅ " if group.chat_id in selected_chat_ids else "⬜ ") + group.title,
                    callback_data=f"secure:{toggle_token}",
                ),
                InlineKeyboardButton(
                    text="主群" if group.chat_id == primary_chat_id else "设为主群",
                    callback_data=f"secure:{primary_token}",
                ),
            ]
            for group, (toggle_token, primary_token) in zip(groups, token_pairs, strict=True)
        ]
        back = await self._keyboard(
            target,
            [[("✅ 完成选择", "draft_groups_done")], [("↩ 返回上一层", "draft_back")]],
            context=context,
        )
        rows.extend(back.inline_keyboard)
        await self._bot.edit_message_text(
            f"👥 参与活动的群（已选 {len(selected_chat_ids)}）\n\n"
            "参与者必须同时加入全部已选群；每个群都会收到活动卡。",
            chat_id=target.chat_id,
            message_id=target.message_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

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
