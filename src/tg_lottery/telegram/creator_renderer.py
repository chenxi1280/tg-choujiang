from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

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
    ) -> None:
        self._bot = bot
        self._tokens = tokens
        self._queries = queries

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

    async def render_groups(self, message: Message, *, user_id: int) -> None:
        await self.edit_groups(PanelTarget(user_id, message.chat.id, message.message_id))

    async def edit_groups(self, target: PanelTarget) -> None:
        groups = await self._queries.admin_groups(target.user_id)
        requests = tuple(
            CallbackRequest("draft_select_group", payload={"chat_id": group.chat_id})
            for group in groups
        )
        tokens = await self._create_tokens(target, requests)
        rows = [
            [InlineKeyboardButton(text=group.title, callback_data=f"secure:{token}")]
            for group, token in zip(groups, tokens, strict=True)
        ]
        back = await self._keyboard(target, [[("↩ 返回上一层", "draft_back")]])
        rows.extend(back.inline_keyboard)
        await self._bot.edit_message_text(
            "👥 创建抽奖·选择活动群（必填项）\n\n请选择发布抽奖的群：",
            chat_id=target.chat_id,
            message_id=target.message_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _keyboard(self, target: PanelTarget, rows: ButtonRows) -> InlineKeyboardMarkup:
        requests = tuple(CallbackRequest(action) for row in rows for _, action in row)
        tokens = iter(await self._create_tokens(target, requests))
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
        self, target: PanelTarget, requests: tuple[CallbackRequest, ...]
    ) -> tuple[str, ...]:
        return await self._tokens.create_many(
            owner_user_id=target.user_id,
            chat_id=target.chat_id,
            requests=requests,
            expires_in=TOKEN_LIFETIME,
        )
