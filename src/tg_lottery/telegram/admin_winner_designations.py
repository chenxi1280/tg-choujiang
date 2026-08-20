from __future__ import annotations

from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.application.queries import QueryService
from tg_lottery.application.winner_designations import (
    WinnerDesignationService,
    WinnerDesignationView,
)
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Prize
from tg_lottery.telegram.admin_authorization import verify_chat_admin

ACTION = "designate_winner"
_SESSION_STATE = "winner_designation:input"


class AdminWinnerDesignationFlow:
    def __init__(
        self,
        *,
        bot: Bot,
        sessions: AdminSessionService,
        service: WinnerDesignationService,
        queries: QueryService,
    ) -> None:
        self._bot = bot
        self._sessions = sessions
        self._service = service
        self._queries = queries

    async def begin(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        previous = await self._sessions.get(query.from_user.id)
        data: dict[str, object] = {"campaign_id": str(campaign_id)}
        if previous is not None:
            data["previous_session"] = {
                "state": previous.state,
                "data": dict(previous.data),
            }
        await self._sessions.set(query.from_user.id, _SESSION_STATE, data)
        prizes = tuple(await self._queries.prizes(campaign_id))
        rows = await self._service.list(campaign_id)
        await _message(query).answer(_prompt(prizes, rows))

    async def handle_text(self, message: Message) -> bool:
        session = await self._sessions.get(message.from_user.id)
        if session is None or session.state != _SESSION_STATE:
            return False
        campaign_id = UUID(str(session.data["campaign_id"]))
        await self._verify_admin(campaign_id, message.from_user.id)
        text = (message.text or "").strip()
        if text == "取消":
            await self._restore(message.from_user.id, dict(session.data))
            await message.answer("已取消指定中奖操作。")
            return True
        result = await self._apply(campaign_id, text, actor_user_id=message.from_user.id)
        await self._restore(message.from_user.id, dict(session.data))
        await message.answer(result)
        return True

    async def _apply(self, campaign_id: UUID, text: str, *, actor_user_id: int) -> str:
        parts = text.split()
        if parts and parts[0] == "删除":
            if len(parts) != 3:
                raise UserFacingError("删除格式：删除 奖品顺位 名额")
            position, slot = _positions(parts[1], parts[2])
            removed = await self._service.remove(
                campaign_id=campaign_id,
                prize_position=position,
                prize_slot_no=slot,
                actor_user_id=actor_user_id,
            )
            return "已删除该名额的指定中奖人。" if removed else "该名额尚未指定中奖人。"
        if len(parts) != 3:
            raise UserFacingError("指定格式：奖品顺位 名额 @用户名或用户ID")
        position, slot = _positions(parts[0], parts[1])
        row = await self._service.set(
            campaign_id=campaign_id,
            prize_position=position,
            prize_slot_no=slot,
            participant_reference=parts[2],
            actor_user_id=actor_user_id,
        )
        return f"已指定：第{row.prize_position}顺位·名额{row.prize_slot_no} → {_participant(row)}"

    async def _verify_admin(self, campaign_id: UUID, user_id: int) -> None:
        group = await self._queries.campaign_group(campaign_id)
        await verify_chat_admin(self._bot, group.chat_id, user_id)

    async def _restore(self, user_id: int, data: dict[str, Any]) -> None:
        previous = data.get("previous_session")
        if not isinstance(previous, dict):
            await self._sessions.clear(user_id)
            return
        state = str(previous.get("state", ""))
        previous_data = previous.get("data")
        if not state or not isinstance(previous_data, dict):
            await self._sessions.clear(user_id)
            return
        await self._sessions.set(user_id, state, dict(previous_data))


def _prompt(prizes: tuple[Prize, ...], rows: tuple[WinnerDesignationView, ...]) -> str:
    lines = [
        "🎯 指定中奖人",
        "",
        "可指定名额：",
        *(_prize_line(prize) for prize in prizes),
        "",
        "回复：奖品顺位 名额 @用户名或用户ID",
        "例如：1 1 @alice",
        "删除：删除 1 1",
        "退出：取消",
        "",
        "指定用户必须已经报名，并在开奖时通过最终资格复核。",
    ]
    if rows:
        lines.extend(["", "当前指定：", *(_designation_line(row) for row in rows)])
    return "\n".join(lines)


def _prize_line(prize: Prize) -> str:
    return f"• 第{prize.position}顺位 {prize.name}：名额 1-{prize.winner_count}"


def _designation_line(row: WinnerDesignationView) -> str:
    return (
        f"• 第{row.prize_position}顺位 {row.prize_name} · "
        f"名额{row.prize_slot_no}：{_participant(row)}"
    )


def _participant(row: WinnerDesignationView) -> str:
    suffix = (
        f"@{row.participant_username}" if row.participant_username else str(row.participant_user_id)
    )
    return f"{row.participant_display_name}（{suffix}）"


def _positions(raw_position: str, raw_slot: str) -> tuple[int, int]:
    if not raw_position.isdigit() or not raw_slot.isdigit():
        raise UserFacingError("奖品顺位和名额必须是正整数")
    position, slot = int(raw_position), int(raw_slot)
    if position < 1 or slot < 1:
        raise UserFacingError("奖品顺位和名额必须是正整数")
    return position, slot


def _campaign_id(grant: CallbackGrant) -> UUID:
    if grant.campaign_id is None:
        raise ValueError("callback is missing campaign binding")
    return grant.campaign_id


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message
