from __future__ import annotations

import logging
from collections.abc import Sequence

from aiogram.enums import ChatType
from aiogram.types import Message

from tg_lottery.application.queries import QueryService
from tg_lottery.application.winner_designations import (
    DesignationCampaignView,
    WinnerDesignationService,
    WinnerDesignationView,
)
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, Prize
from tg_lottery.telegram.public_projection import campaign_state_text

_HELP_TEXT = """隔离开奖运维入口

/campaigns
/winner_list <活动编号>
/winner_set <活动编号> <奖品顺位> <名额> <@用户名或用户ID>
/winner_del <活动编号> <奖品顺位> <名额>

此入口只设置后台指定记录，不会触发截止、复核、结算或开奖。"""


class OpsWinnerDesignationHandler:
    def __init__(
        self,
        *,
        service: WinnerDesignationService,
        queries: QueryService,
        source_bot_id: int | None,
    ) -> None:
        self._service = service
        self._queries = queries
        self._source_bot_id = source_bot_id
        self._logger = logging.getLogger(__name__)

    async def handle(self, message: Message) -> None:
        user = message.from_user
        if message.chat.type != ChatType.PRIVATE:
            self._logger.warning("ops bot rejected non-private message")
            return
        if user is None or user.is_bot:
            self._logger.warning("ops bot rejected unauthorized user")
            await message.answer("无权访问此运维入口。")
            return
        try:
            result = await self.execute((message.text or "").strip(), actor_user_id=user.id)
        except UserFacingError as exc:
            await message.answer(f"操作失败：{exc.public_message}")
            return
        await message.answer(result)

    async def execute(self, text: str, *, actor_user_id: int) -> str:
        await self._service.require_authorized(actor_user_id)
        return await self._dispatch(text, actor_user_id=actor_user_id)

    async def _dispatch(self, text: str, *, actor_user_id: int) -> str:
        command, arguments = _command(text)
        if command in {"/start", "/help"}:
            return _HELP_TEXT
        if command == "/campaigns":
            _require_argument_count(arguments, 0, command)
            return _campaigns_text(
                await self._service.editable_campaigns(actor_user_id=actor_user_id)
            )
        if command == "/winner_list":
            return await self._list(arguments, actor_user_id=actor_user_id)
        if command == "/winner_set":
            return await self._set(arguments, actor_user_id=actor_user_id)
        if command == "/winner_del":
            return await self._remove(arguments, actor_user_id=actor_user_id)
        raise UserFacingError("不支持的运维命令，请发送 /help 查看格式")

    async def _list(self, arguments: Sequence[str], *, actor_user_id: int) -> str:
        _require_argument_count(arguments, 1, "/winner_list")
        campaign = await self._campaign(arguments[0])
        prizes = tuple(await self._queries.prizes(campaign.id))
        rows = await self._service.list(campaign.id, actor_user_id=actor_user_id)
        return _designation_text(campaign, prizes, rows)

    async def _set(self, arguments: Sequence[str], *, actor_user_id: int) -> str:
        _require_argument_count(arguments, 4, "/winner_set")
        campaign = await self._campaign(arguments[0])
        position, slot = _positions(arguments[1], arguments[2])
        row = await self._service.set(
            campaign_id=campaign.id,
            prize_position=position,
            prize_slot_no=slot,
            participant_reference=arguments[3],
            actor_user_id=actor_user_id,
            source_bot_id=self._source_bot_id,
        )
        return f"已写入后台指定：{_designation_line(row)}"

    async def _remove(self, arguments: Sequence[str], *, actor_user_id: int) -> str:
        _require_argument_count(arguments, 3, "/winner_del")
        campaign = await self._campaign(arguments[0])
        position, slot = _positions(arguments[1], arguments[2])
        removed = await self._service.remove(
            campaign_id=campaign.id,
            prize_position=position,
            prize_slot_no=slot,
            actor_user_id=actor_user_id,
            source_bot_id=self._source_bot_id,
        )
        return "已删除后台指定记录。" if removed else "该名额没有后台指定记录。"

    async def _campaign(self, reference: str) -> Campaign:
        campaign = await self._queries.campaign_by_reference(reference)
        if campaign is None:
            raise UserFacingError("活动编号不存在")
        return campaign


def _command(text: str) -> tuple[str, tuple[str, ...]]:
    parts = text.split()
    if not parts:
        raise UserFacingError("请输入运维命令")
    command = parts[0].partition("@")[0].lower()
    return command, tuple(parts[1:])


def _require_argument_count(arguments: Sequence[str], expected: int, command: str) -> None:
    if len(arguments) != expected:
        raise UserFacingError(f"{command} 参数数量不正确，请发送 /help 查看格式")


def _positions(raw_position: str, raw_slot: str) -> tuple[int, int]:
    if not raw_position.isdigit() or not raw_slot.isdigit():
        raise UserFacingError("奖品顺位和名额必须是正整数")
    position, slot = int(raw_position), int(raw_slot)
    if position < 1 or slot < 1:
        raise UserFacingError("奖品顺位和名额必须是正整数")
    return position, slot


def _campaigns_text(rows: tuple[DesignationCampaignView, ...]) -> str:
    if not rows:
        return "当前没有可设置的活动。"
    lines = ["可设置活动："]
    lines.extend(
        f"• {row.name} · {row.public_ref} · {campaign_state_text(row.state)}" for row in rows
    )
    return "\n".join(lines)


def _designation_text(
    campaign: Campaign,
    prizes: tuple[Prize, ...],
    rows: tuple[WinnerDesignationView, ...],
) -> str:
    lines = [f"活动：{campaign.name} · {campaign.public_ref}", "", "可用名额："]
    lines.extend(
        f"• 第{prize.position}顺位 {prize.name}：1-{prize.winner_count}" for prize in prizes
    )
    lines.extend(["", "当前后台指定："])
    lines.extend(_designation_line(row) for row in rows)
    if not rows:
        lines.append("无")
    return "\n".join(lines)


def _designation_line(row: WinnerDesignationView) -> str:
    identity = (
        f"@{row.participant_username}" if row.participant_username else str(row.participant_user_id)
    )
    return (
        f"第{row.prize_position}顺位·名额{row.prize_slot_no} → "
        f"{row.participant_display_name}（{identity}）"
    )
