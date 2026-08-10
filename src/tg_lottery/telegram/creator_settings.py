from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram.types import CallbackQuery, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer, PanelTarget
from tg_lottery.telegram.creator_schedule import positive_int, quick_draw_at, validate_draw_time
from tg_lottery.telegram.creator_views import (
    dashboard_rows,
    draw_prompt_text,
    preview_text,
    rules_text,
)
from tg_lottery.telegram.draft_config import (
    DEFAULT_CLAIM_INSTRUCTIONS,
    LOCAL_TIME_FORMAT,
    parse_draw_at,
)
from tg_lottery.telegram.personal_flow import REGIONS
from tg_lottery.telegram.public_projection import public_timezone_label

Handler = Callable[[CallbackQuery, CallbackGrant], Awaitable[None]]
TextHandler = Callable[[Message, dict[str, Any]], Awaitable[None]]
MAX_REQUIRED_CHATS = 10


class CreatorSettings:
    def __init__(
        self,
        *,
        sessions: AdminSessionService,
        renderer: CreatorRenderer,
        profiles: UserProfileService,
    ) -> None:
        self._sessions = sessions
        self._renderer = renderer
        self._profiles = profiles

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> bool:
        handler = self._grant_handlers().get(grant.action)
        if handler is None:
            return False
        await handler(query, grant)
        return True

    async def handle_message(self, message: Message, state: str, data: dict[str, Any]) -> bool:
        handler = self._text_handlers().get(state)
        if handler is None:
            return False
        await handler(message, data)
        return True

    def _grant_handlers(self) -> dict[str, Handler]:
        return {
            "draft_draw": self._show_draw_modes,
            "draft_draw_fixed": self._show_fixed_draw,
            "draft_draw_full": self._edit_full_draw,
            "draft_draw_hour": self._set_quick_draw,
            "draft_draw_evening": self._set_quick_draw,
            "draft_draw_tomorrow": self._set_quick_draw,
            "draft_draw_custom": self._edit_custom_draw,
            "draft_claim": self._edit_claim,
            "draft_claim_default": self._default_claim,
            "draft_rules": self._show_luck,
            "draft_rules_off": self._set_luck,
            "draft_rules_standard": self._set_luck,
            "draft_rules_active": self._set_luck,
            "draft_cover": self._edit_cover,
            "draft_cover_clear": self._clear_cover,
            "draft_user_limits": self._show_user_limits,
            "draft_username_on": self._set_username,
            "draft_username_off": self._set_username,
            "draft_regions": self._edit_regions,
            "draft_participation": self._edit_participation,
            "draft_description": self._edit_description,
            "draft_issuer": self._edit_issuer,
            "draft_messages": self._show_messages,
            "draft_messages_on": self._set_messages,
            "draft_messages_off": self._set_messages,
            "draft_load_last": self._load_last,
        }

    def _text_handlers(self) -> dict[str, TextHandler]:
        return {
            "draft:draw": self._receive_draw,
            "draft:full_target": self._receive_full_target,
            "draft:claim": self._receive_claim,
            "draft:cover": self._receive_cover,
            "draft:regions": self._receive_regions,
            "draft:participation": self._receive_participation,
            "draft:description": self._receive_description,
            "draft:issuer": self._receive_issuer,
        }

    async def _show_draw_modes(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        rows = [[("满人开奖", "draft_draw_full"), ("定时开奖", "draft_draw_fixed")], *_back_rows()]
        await self._render(query, data=data, text=draw_prompt_text(data), rows=rows)

    async def _show_fixed_draw(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        await self._render(
            query, data=data, text="🕒 定时开奖\n\n请选择快捷时间或自定义时间：", rows=_draw_rows()
        )

    async def _edit_full_draw(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:full_target")
        await self._render(
            query,
            data=data,
            text="👥 满人开奖\n\n请回复达到多少名合格参与者后开奖：",
            rows=_back_rows(),
        )

    async def _edit_custom_draw(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:draw")
        await self._render(
            query,
            data=data,
            text=custom_draw_prompt(str(data["timezone"])),
            rows=_back_rows(),
        )

    async def _set_quick_draw(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        draw_at = quick_draw_at(grant.action, str(data["timezone"]))
        await self._dashboard(query, data | {"draw_mode": "fixed", "draw_at": draw_at.isoformat()})

    async def _receive_draw(self, message: Message, data: dict[str, Any]) -> None:
        draw_at = parse_draw_at(message.text or "", str(data["timezone"]))
        validate_draw_time(draw_at)
        await self._dashboard_from_message(
            message, data | {"draw_mode": "fixed", "draw_at": draw_at.isoformat()}
        )

    async def _receive_full_target(self, message: Message, data: dict[str, Any]) -> None:
        target = positive_int(message.text or "", "参与人数")
        await self._dashboard_from_message(
            message, data | {"draw_mode": "full", "participant_target": target}
        )

    async def _edit_claim(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:claim")
        rows = [[("使用默认说明", "draft_claim_default")], *_back_rows()]
        await self._render(
            query, data=data, text="⚠️ 请回复中奖后的领取方式（最多 500 字）：", rows=rows
        )

    async def _receive_claim(self, message: Message, data: dict[str, Any]) -> None:
        await self._receive_text(
            message, data, key="claim_instructions", label="领奖说明", limit=500
        )

    async def _default_claim(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._update(query, {"claim_instructions": DEFAULT_CLAIM_INSTRUCTIONS})

    async def _show_luck(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        rows = [
            [
                ("关闭", "draft_rules_off"),
                ("标准", "draft_rules_standard"),
                ("高活跃", "draft_rules_active"),
            ],
            *_back_rows(),
        ]
        await self._render(query, data=data, text=rules_text(data), rows=rows)

    async def _set_luck(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        mode = grant.action.removeprefix("draft_rules_")
        preset = "active" if mode == "active" else "standard"
        await self._update(query, {"luck_mode": mode, "activity_preset": preset})

    async def _edit_cover(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:cover")
        rows = [[("清除封面", "draft_cover_clear")], *_back_rows()]
        await self._render(query, data=data, text="🏞️ 请发送一张图片作为抽奖封面：", rows=rows)

    async def _receive_cover(self, message: Message, data: dict[str, Any]) -> None:
        if not message.photo:
            raise UserFacingError("封面必须发送 Telegram 图片")
        await self._dashboard_from_message(
            message, data | {"cover_file_id": message.photo[-1].file_id}
        )

    async def _clear_cover(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._update(query, {"cover_file_id": None})

    async def _show_user_limits(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        rows = [
            [("用户名必填", "draft_username_on"), ("用户名可为空", "draft_username_off")],
            [("设置地区", "draft_regions")],
            *_back_rows(),
        ]
        await self._render(
            query, data=data, text="🪪 用户限制\n\n可要求用户名，并限制最多 3 个地区。", rows=rows
        )

    async def _set_username(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._update(query, {"username_required": grant.action.endswith("on")})

    async def _edit_regions(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:regions")
        await self._render(
            query,
            data=data,
            text="🌏 回复 1–3 个地区，用逗号分隔；回复 全国 清除限制。",
            rows=_back_rows(),
        )

    async def _receive_regions(self, message: Message, data: dict[str, Any]) -> None:
        text = (message.text or "").strip()
        regions = [] if text == "全国" else _split_values(text)
        if len(regions) > 3 or any(region not in REGIONS for region in regions):
            raise UserFacingError("地区必须是省级选项且最多 3 个")
        await self._dashboard_from_message(message, data | {"allowed_regions": regions})

    async def _edit_participation(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:participation")
        text = (
            "📮 回复额外必加群/频道公开用户名或数字 ID，用逗号分隔；回复 无 清除。\n"
            "主活动群始终是参与条件。"
        )
        await self._render(query, data=data, text=text, rows=_back_rows())

    async def _receive_participation(self, message: Message, data: dict[str, Any]) -> None:
        text = (message.text or "").strip()
        values = [] if text == "无" else _parse_chat_refs(text)
        if len(values) > MAX_REQUIRED_CHATS:
            raise UserFacingError(f"额外参与条件最多 {MAX_REQUIRED_CHATS} 个")
        await self._dashboard_from_message(message, data | {"required_chat_refs": values})

    async def _edit_description(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:description")
        await self._render(
            query, data=data, text="⚠️ 请回复抽奖说明（最多 500 字）：", rows=_back_rows()
        )

    async def _receive_description(self, message: Message, data: dict[str, Any]) -> None:
        await self._receive_text(message, data, key="description", label="抽奖说明", limit=500)

    async def _edit_issuer(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:issuer")
        await self._render(
            query, data=data, text="🎅 回复发奖人员用户名或用户 ID：", rows=_back_rows()
        )

    async def _receive_issuer(self, message: Message, data: dict[str, Any]) -> None:
        value = (message.text or "").strip()
        if not 1 <= len(value) <= 64:
            raise UserFacingError("发奖人员长度必须为 1–64 字")
        await self._dashboard_from_message(message, data | {"issuer": value, "issuer_name": value})

    async def _show_messages(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        rows = [[("开启", "draft_messages_on"), ("关闭", "draft_messages_off")], *_back_rows()]
        await self._render(
            query, data=data, text="🔔 是否在必要状态与结果后附加详细说明？", rows=rows
        )

    async def _set_messages(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._update(query, {"message_tips": grant.action.endswith("on")})

    async def _load_last(self, query: CallbackQuery, _: CallbackGrant) -> None:
        current = await self._required_data(query.from_user.id)
        last_draft = await self._profiles.recent_last_draft(query.from_user.id, utcnow())
        if not last_draft:
            raise UserFacingError("没有可载入的上次抽奖配置")
        protected = {key: current[key] for key in ("panel_message_id", "creator_name")}
        await self._dashboard(query, last_draft | protected | {"guided": False})

    async def _receive_text(
        self,
        message: Message,
        data: dict[str, Any],
        *,
        key: str,
        label: str,
        limit: int,
    ) -> None:
        value = (message.text or "").strip()
        if not 1 <= len(value) <= limit:
            raise UserFacingError(f"{label}长度必须为 1–{limit} 字")
        await self._dashboard_from_message(message, data | {key: value})

    async def _update(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        await self._dashboard(query, await self._required_data(query.from_user.id) | changes)

    async def _dashboard(self, query: CallbackQuery, data: dict[str, Any]) -> None:
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await self._render(query, data=data, text=preview_text(data), rows=dashboard_rows())

    async def _dashboard_from_message(self, message: Message, data: dict[str, Any]) -> None:
        user_id = _user_id(message)
        await self._sessions.set(user_id, "draft:idle", data)
        await self._renderer.edit(
            _target(message, data), text=preview_text(data), rows=dashboard_rows()
        )

    async def _set_state(self, query: CallbackQuery, state: str) -> dict[str, Any]:
        data = await self._required_data(query.from_user.id)
        updated = data | {"panel_message_id": _message(query).message_id, "guided": False}
        await self._sessions.set(query.from_user.id, state, updated)
        return updated

    async def _required_data(self, user_id: int) -> dict[str, Any]:
        session = await self._sessions.get(user_id)
        if session is None or not session.state.startswith("draft:"):
            raise UserFacingError("创建草稿已过期，请发送 /new 重新创建")
        return dict(session.data)

    async def _render(
        self,
        query: CallbackQuery,
        *,
        data: dict[str, Any],
        text: str,
        rows: ButtonRows,
    ) -> None:
        await self._renderer.render(
            _message(query), user_id=query.from_user.id, text=text, rows=rows
        )


def _parse_chat_refs(value: str) -> list[int | str]:
    refs: list[int | str] = []
    for item in _split_values(value):
        if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", item):
            refs.append(item)
            continue
        try:
            refs.append(int(item))
        except ValueError as exc:
            raise UserFacingError("群/频道需填写公开用户名或数字 ID") from exc
    return list(dict.fromkeys(refs))


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、\s]+", value) if item.strip()]


def custom_draw_prompt(timezone: str, *, now: datetime | None = None) -> str:
    example = quick_draw_at("draft_draw_tomorrow", timezone, now=now)
    label = public_timezone_label(timezone)
    return f"🕒 回复开奖时间 ({label})\n\n例如: {example.strftime(LOCAL_TIME_FORMAT)}"


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message


def _user_id(message: Message) -> int:
    if message.from_user is None:
        raise ValueError("无法识别当前用户")
    return message.from_user.id


def _target(message: Message, data: dict[str, Any]) -> PanelTarget:
    return PanelTarget(_user_id(message), message.chat.id, int(data["panel_message_id"]))


def _back_rows() -> ButtonRows:
    return [[("↩ 返回上一层", "draft_back")]]


def _draw_rows() -> ButtonRows:
    return [
        [("1小时后", "draft_draw_hour"), ("今晚20:00", "draft_draw_evening")],
        [("明晚20:00", "draft_draw_tomorrow"), ("自定义时间", "draft_draw_custom")],
        *_back_rows(),
    ]
