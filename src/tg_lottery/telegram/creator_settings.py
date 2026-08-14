from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_bonus as bonus
from tg_lottery.telegram import creator_participation as participation
from tg_lottery.telegram.creator_messages import (
    MESSAGE_DELAYS,
    message_settings_rows,
    message_settings_text,
)
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer
from tg_lottery.telegram.creator_schedule import positive_int, quick_draw_at, validate_draw_time
from tg_lottery.telegram.creator_settings_ui import (
    _back_rows,
    _draw_rows,
    _message,
    _required_user,
    _target,
    _user_id,
    custom_draw_prompt,
)
from tg_lottery.telegram.creator_views import (
    dashboard_rows,
    draw_prompt_text,
    preview_text,
)
from tg_lottery.telegram.draft_config import (
    DEFAULT_CLAIM_INSTRUCTIONS,
    ensure_draft_identity,
    parse_draw_at,
)
from tg_lottery.telegram.issuer import resolve_issuer
from tg_lottery.telegram.personal_flow import REGIONS
from tg_lottery.telegram.required_chats import resolve_required_chat

Handler = Callable[[CallbackQuery, CallbackGrant], Awaitable[None]]
TextHandler = Callable[[Message, dict[str, Any]], Awaitable[None]]


class CreatorSettings:
    def __init__(
        self,
        *,
        sessions: AdminSessionService,
        renderer: CreatorRenderer,
        profiles: UserProfileService,
        queries: QueryService,
        bot: Bot,
    ) -> None:
        self._sessions = sessions
        self._renderer = renderer
        self._profiles = profiles
        self._queries = queries
        self._bot = bot

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> bool:
        handler = self._grant_handlers().get(grant.action)
        if handler is None:
            return False
        await handler(query, grant)
        return True

    def handles_action(self, action: str) -> bool:
        return action in self._grant_handlers()

    async def handle_message(self, message: Message, state: str, data: dict[str, Any]) -> bool:
        handler = self._text_handlers().get(state)
        if handler is None:
            return False
        await handler(message, data)
        return True

    def _grant_handlers(self) -> dict[str, Handler]:
        handlers = {
            "draft_draw": self._show_draw_modes,
            "draft_draw_fixed": self._show_fixed_draw,
            "draft_draw_full": self._edit_full_draw,
            "draft_draw_hour": self._set_quick_draw,
            "draft_draw_evening": self._set_quick_draw,
            "draft_draw_tomorrow": self._set_quick_draw,
            "draft_draw_custom": self._edit_custom_draw,
            "draft_claim": self._edit_claim,
            "draft_claim_default": self._default_claim,
            "draft_rules": self._edit_bonus,
            "draft_cover": self._edit_cover,
            "draft_cover_clear": self._clear_cover,
            "draft_user_limits": self._show_user_limits,
            "draft_username_on": self._set_username,
            "draft_username_off": self._set_username,
            "draft_regions": self._edit_regions,
            "draft_participation": self._edit_participation,
            "draft_participation_add": self._edit_participation_add,
            "draft_participation_prev": self._page_participation,
            "draft_participation_next": self._page_participation,
            "draft_participation_delete": self._edit_participation_delete,
            "draft_participation_clear": self._clear_participation,
            "draft_description": self._edit_description,
            "draft_issuer": self._edit_issuer,
            "draft_messages": self._show_messages,
            "draft_messages_on": self._set_messages,
            "draft_messages_off": self._set_messages,
            "draft_load_last": self._load_last,
        }
        handlers.update(
            {
                "draft_messages_trigger_on": self._set_message_trigger,
                "draft_messages_trigger_off": self._set_message_trigger,
            }
        )
        handlers.update(
            {f"draft_messages_delay_{key}": self._set_message_delay for key in MESSAGE_DELAYS}
        )
        handlers.update({action: self._change_bonus for action in bonus.BONUS_ACTIONS})
        return handlers

    def _text_handlers(self) -> dict[str, TextHandler]:
        return {
            "draft:draw": self._receive_draw,
            "draft:full_target": self._receive_full_target,
            "draft:claim": self._receive_claim,
            "draft:cover": self._receive_cover,
            "draft:regions": self._receive_regions,
            "draft:participation": self._receive_participation,
            "draft:participation_delete": self._receive_participation_delete,
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

    async def _edit_bonus(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        text, rows = bonus.bonus_view(data, "")
        await self._render(query, data=data, text=text, rows=rows)

    async def _change_bonus(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        data = bonus.apply_bonus_action(await self._required_data(query.from_user.id), grant.action)
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        text, rows = bonus.bonus_view(data, grant.action)
        await self._render(query, data=data, text=text, rows=rows)

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
        regions = [] if text == "全国" else participation.split_values(text)
        if len(regions) > 3 or any(region not in REGIONS for region in regions):
            raise UserFacingError("地区必须是省级选项且最多 3 个")
        await self._dashboard_from_message(message, data | {"allowed_regions": regions})

    async def _edit_participation(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:participation")
        await self._render(
            query,
            data=data,
            text=participation.participation_text(data),
            rows=participation.participation_rows(data),
        )

    async def _edit_participation_add(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:participation")
        await self._render(
            query,
            data=data,
            text=participation.participation_add_text(),
            rows=[[("↩ 返回条件列表", "draft_participation")]],
        )

    async def _receive_participation(self, message: Message, data: dict[str, Any]) -> None:
        if data.get("chat_id") is None:
            raise UserFacingError("请先在抽奖预览中选择活动群")
        values = participation.parse_chat_refs((message.text or "").strip())
        if not values:
            raise UserFacingError("请至少填写一个群或频道")
        chats = list(data.get("required_chats") or [])
        existing_ids = {int(item["id"]) for item in chats}
        existing_ids.add(int(data["chat_id"]))
        added = []
        for value in values:
            chat = await resolve_required_chat(self._bot, value)
            if int(chat["id"]) in existing_ids:
                raise UserFacingError("该群或频道已经在参与条件中")
            existing_ids.add(int(chat["id"]))
            added.append(chat)
        updated_chats = [*chats, *added]
        updated = _with_required_chats(data, updated_chats) | {
            "participation_page": (len(updated_chats) - 1) // participation.PARTICIPATION_PAGE_SIZE,
        }
        await self._sessions.set(_user_id(message), "draft:participation", updated)
        await self._renderer.edit(
            _target(message, updated),
            text=participation.participation_text(updated),
            rows=participation.participation_rows(updated),
        )

    async def _page_participation(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        delta = -1 if grant.action.endswith("prev") else 1
        updated = data | {"participation_page": participation.participation_page(data) + delta}
        await self._sessions.set(query.from_user.id, "draft:participation", updated)
        await self._render(
            query,
            data=updated,
            text=participation.participation_text(updated),
            rows=participation.participation_rows(updated),
        )

    async def _edit_participation_delete(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:participation_delete")
        chats = list(data.get("required_chats") or [])
        if not chats:
            raise UserFacingError("当前没有可删除的额外参与条件")
        await self._render(
            query,
            data=data,
            text=participation.participation_delete_text(data),
            rows=_back_rows(),
        )

    async def _receive_participation_delete(self, message: Message, data: dict[str, Any]) -> None:
        chats = list(data.get("required_chats") or [])
        positions = set(participation.parse_positions(message.text or "", len(chats)))
        kept = [chat for index, chat in enumerate(chats, start=1) if index not in positions]
        updated = _with_required_chats(data, kept) | {
            "participation_page": participation.participation_page(data | {"required_chats": kept}),
        }
        await self._sessions.set(_user_id(message), "draft:participation", updated)
        await self._renderer.edit(
            _target(message, updated),
            text=participation.participation_text(updated),
            rows=participation.participation_rows(updated),
        )

    async def _clear_participation(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        updated = _with_required_chats(data, []) | {"participation_page": 0}
        await self._sessions.set(query.from_user.id, "draft:participation", updated)
        await self._render(
            query,
            data=updated,
            text=participation.participation_text(updated),
            rows=participation.participation_rows(updated),
        )

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
            query,
            data=data,
            text="🎅 回复已参与用户的 @username，或 Telegram 用户 ID：",
            rows=_back_rows(),
        )

    async def _receive_issuer(self, message: Message, data: dict[str, Any]) -> None:
        value = (message.text or "").strip()
        if not 1 <= len(value) <= 64:
            raise UserFacingError("发奖人员长度必须为 1–64 字")
        candidates = (
            await self._queries.user_ids_by_username(value) if value.startswith("@") else ()
        )
        issuer = await resolve_issuer(
            self._bot, value, _required_user(message), candidate_user_ids=candidates
        )
        await self._dashboard_from_message(message, data | issuer)

    async def _show_messages(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        await self._render(
            query,
            data=data,
            text=message_settings_text(data),
            rows=message_settings_rows(),
        )

    async def _set_messages(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._update(query, {"message_tips": grant.action.endswith("on")})

    async def _set_message_trigger(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._update(query, {"delete_trigger_message": grant.action.endswith("on")})

    async def _set_message_delay(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        key = grant.action.removeprefix("draft_messages_delay_")
        await self._update(query, {"completion_delete_after_seconds": MESSAGE_DELAYS[key]})

    async def _load_last(self, query: CallbackQuery, _: CallbackGrant) -> None:
        current = await self._required_data(query.from_user.id)
        last_draft = await self._profiles.recent_last_draft(query.from_user.id, utcnow())
        if not last_draft:
            raise UserFacingError("没有可载入的上次抽奖配置")
        protected = {
            key: current[key]
            for key in (
                "draft_id",
                "draft_revision",
                "panel_message_id",
                "creator_name",
            )
        }
        restored = await self._restore_required_chats(ensure_draft_identity(last_draft | protected))
        await self._dashboard(query, restored | {"guided": False})

    async def _restore_required_chats(self, data: dict[str, Any]) -> dict[str, Any]:
        records = list(data.get("required_chats") or [])
        if not records:
            refs = data.get("required_chat_refs") or data.get("required_chat_ids") or []
            records = [await resolve_required_chat(self._bot, ref) for ref in refs]
        return _with_required_chats(data, records)

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


def _with_required_chats(data: dict[str, Any], chats: list[dict[str, Any]]) -> dict[str, Any]:
    chat_ids = [int(item["id"]) for item in chats]
    return data | {
        "required_chats": chats,
        "required_chat_refs": chat_ids,
        "required_chat_ids": chat_ids,
    }
