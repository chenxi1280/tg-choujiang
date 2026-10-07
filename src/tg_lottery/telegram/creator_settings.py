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
from tg_lottery.telegram import creator_group_bonus as group_bonus
from tg_lottery.telegram import creator_participation as participation
from tg_lottery.telegram.creator_admission_picker import (
    PREFIX as ADMISSION_PREFIX,
)
from tg_lottery.telegram.creator_admission_picker import (
    CreatorAdmissionPicker,
    add_admission_chats,
)
from tg_lottery.telegram.creator_bonus_permissions import verify_bonus_action
from tg_lottery.telegram.creator_cover import CreatorCover
from tg_lottery.telegram.creator_current_view import current_draft_view
from tg_lottery.telegram.creator_gate_schedule import validate_gate_time
from tg_lottery.telegram.creator_messages import (
    MESSAGE_DELAYS,
    message_settings_rows,
    message_settings_text,
)
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer
from tg_lottery.telegram.creator_required_chats_data import (
    required_draft_data,
    validated_text_changes,
    with_required_chats,
)
from tg_lottery.telegram.creator_retention_revision import synchronize_retention
from tg_lottery.telegram.creator_reuse_flow import CreatorReuse
from tg_lottery.telegram.creator_schedule import quick_draw_at, validate_draw_time
from tg_lottery.telegram.creator_sections import enter_section, section_view
from tg_lottery.telegram.creator_settings_ui import (
    _back_rows,
    _message,
    _required_user,
    _target,
    _user_id,
    custom_draw_prompt,
    custom_draw_rows,
)
from tg_lottery.telegram.creator_threshold_input import CreatorThresholdInput
from tg_lottery.telegram.draft_config import (
    DEFAULT_CLAIM_INSTRUCTIONS,
    parse_draw_at,
)
from tg_lottery.telegram.issuer import resolve_issuer
from tg_lottery.telegram.personal_flow import REGIONS

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
        platform_policy=None,
    ) -> None:
        self._sessions = sessions
        self._renderer = renderer
        self._profiles = profiles
        self._queries = queries
        self._bot = bot
        self._platform_policy = platform_policy
        self._admission = CreatorAdmissionPicker(
            bot=bot, sessions=sessions, queries=queries, renderer=renderer
        )
        self._cover = CreatorCover(sessions=sessions, renderer=renderer)
        self._reuse = CreatorReuse(sessions=sessions, profiles=profiles, renderer=renderer, bot=bot)
        self._threshold_input = CreatorThresholdInput(sessions=sessions, renderer=renderer)

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> bool:
        if grant.action.startswith(ADMISSION_PREFIX):
            await self._admission.handle(query, grant)
            return True
        if grant.action.startswith("draft_threshold_input:"):
            await self._threshold_input.show(query, grant)
            return True
        if group_bonus.handles_action(grant.action):
            await self._change_bonus(query, grant)
            return True
        if grant.action.startswith("draft_chat_threshold:"):
            await self._change_chat_threshold(query, grant)
            return True
        handler = self._grant_handlers().get(grant.action)
        if handler is None:
            return False
        await handler(query, grant)
        return True

    def handles_action(self, action: str) -> bool:
        return (
            action.startswith(("draft_chat_threshold:", "draft_threshold_input:", ADMISSION_PREFIX))
            or group_bonus.handles_action(action)
            or action in self._grant_handlers()
        )

    async def handle_message(self, message: Message, state: str, data: dict[str, Any]) -> bool:
        if state in {"draft:admission_picker", "draft:admission_search"}:
            await self._admission.receive(
                message, data, searching=state == "draft:admission_search"
            )
            return True
        handler = self._text_handlers().get(state)
        if handler is None:
            return False
        await handler(message, data)
        return True

    def _grant_handlers(self) -> dict[str, Handler]:
        handlers = {
            "draft_draw": self._edit_custom_draw,
            "draft_draw_fixed": self._edit_custom_draw,
            "draft_draw_full": self._edit_full_draw,
            "draft_draw_week": self._set_quick_draw,
            "draft_draw_ten_days": self._set_quick_draw,
            "draft_draw_hour": self._set_quick_draw,
            "draft_draw_evening": self._set_quick_draw,
            "draft_draw_tomorrow": self._set_quick_draw,
            "draft_draw_custom": self._edit_custom_draw,
            "draft_claim": self._edit_claim,
            "draft_claim_default": self._default_claim,
            "draft_rules": self._edit_bonus,
            "draft_cover": self._cover.show,
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
            "draft_load_last": self._reuse.show,
            "draft_load_last_confirm": self._reuse.confirm,
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
            "draft:chat_threshold": self._threshold_input.receive,
            "draft:full_target": self._receive_full_target,
            "draft:claim": self._receive_claim,
            "draft:cover": self._cover.receive,
            "draft:regions": self._receive_regions,
            "draft:participation": self._receive_participation,
            "draft:participation_delete": self._receive_participation_delete,
            "draft:description": self._receive_description,
            "draft:issuer": self._receive_issuer,
            "draft:bonus_input": self._receive_bonus_input,
        }

    async def _edit_full_draw(self, query: CallbackQuery, _: CallbackGrant) -> None:
        raise UserFacingError("满人开奖已取消，请返回开奖方式选择定时开奖")

    async def _edit_custom_draw(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:draw")
        saved = str(data.get("draw_at") or "")
        now = utcnow()
        await self._renderer.render(
            _message(query),
            user_id=query.from_user.id,
            text=custom_draw_prompt(str(data["timezone"]), saved=saved, now=now),
            rows=custom_draw_rows(str(data["timezone"]), saved=saved, now=now),
            parse_mode="HTML",
        )

    async def _set_quick_draw(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        draw_at = quick_draw_at(grant.action, str(data["timezone"]))
        validate_gate_time(data, draw_at=draw_at)
        await self._dashboard(query, data | {"draw_mode": "fixed", "draw_at": draw_at.isoformat()})

    async def _receive_draw(self, message: Message, data: dict[str, Any]) -> None:
        draw_at = parse_draw_at(message.text or "", str(data["timezone"]))
        validate_draw_time(draw_at)
        validate_gate_time(data, draw_at=draw_at)
        await self._dashboard_from_message(
            message, data | {"draw_mode": "fixed", "draw_at": draw_at.isoformat()}
        )

    async def _receive_full_target(self, message: Message, data: dict[str, Any]) -> None:
        raise UserFacingError("满人开奖已取消，请返回开奖方式选择定时开奖")

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
        current = await self._required_data(query.from_user.id)
        await verify_bonus_action(self._bot, query.from_user.id, current, action=grant.action)
        data = bonus.apply_bonus_action(current, grant.action)
        data = synchronize_retention(data)
        if grant.action in {"draft_bonus_invite", "draft_bonus_activity"}:
            kind = grant.action.removeprefix("draft_bonus_")
            data = enter_section(data, "bonus_" + kind) | {"creator_bonus_kind": kind}
        if data.get("invite_retention_hours") != current.get("invite_retention_hours"):
            validate_gate_time(data)
        state = "draft:bonus_input" if data.get("bonus_input_field") else "draft:idle"
        await self._sessions.set(query.from_user.id, state, data)
        text, rows = bonus.bonus_view(data, grant.action)
        if state == "draft:bonus_input":
            text = group_bonus.input_prompt(data)
            rows = [[("取消输入", "draft_bonus_input_cancel")]]
        await self._render(query, data=data, text=text, rows=rows)

    async def _receive_bonus_input(self, message: Message, data: dict[str, Any]) -> None:
        updated = group_bonus.receive_input(data, message.text or "")
        await self._sessions.set(_user_id(message), "draft:idle", updated)
        text, rows = bonus.bonus_view(updated, "")
        await self._renderer.edit(_target(message, updated), text=text, rows=rows)

    async def _clear_cover(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._update(query, {"cover_file_id": None, "guided": False})

    async def _show_user_limits(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = enter_section(await self._set_state(query, "draft:idle"), "user_limits")
        text, rows = section_view(data, "user_limits")
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await self._render(query, data=data, text=text, rows=rows)

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
        data = await self._required_data(query.from_user.id)
        updated = enter_section(data, "requirements") | {
            "panel_message_id": _message(query).message_id,
            "guided": False,
        }
        await self._render_participation(query, updated)

    async def _render_participation(self, query: CallbackQuery, updated: dict[str, Any]) -> None:
        await self._sessions.set(query.from_user.id, "draft:participation", updated)
        await self._render(
            query,
            data=updated,
            text=participation.participation_text(updated),
            rows=participation.participation_rows(updated),
        )

    async def _edit_participation_add(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._admission.show(query, _)

    async def _receive_participation(self, message: Message, data: dict[str, Any]) -> None:
        updated = await add_admission_chats(
            self._bot,
            data,
            refs=participation.message_chat_refs(message),
            user_id=_user_id(message),
        )
        updated = updated | {
            "participation_page": (len(updated["required_chats"]) - 1)
            // participation.PARTICIPATION_PAGE_SIZE,
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
        page = participation.participation_page(data) + delta
        await self._render_participation(query, data | {"participation_page": page})

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
        updated = with_required_chats(data, kept) | {
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
        await self._render_participation(
            query, with_required_chats(data, []) | {"participation_page": 0}
        )

    async def _change_chat_threshold(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        data = await self._required_data(query.from_user.id)
        await self._render_participation(query, participation.apply_threshold(data, grant.action))

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
        data = enter_section(await self._set_state(query, "draft:idle"), "messages")
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await self._render(
            query, data=data, text=message_settings_text(data), rows=message_settings_rows(data)
        )

    async def _set_messages(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._update(query, {"message_tips": grant.action.endswith("on")})

    async def _set_message_trigger(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._update(query, {"delete_trigger_message": grant.action.endswith("on")})

    async def _set_message_delay(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        key = grant.action.removeprefix("draft_messages_delay_")
        await self._update(query, {"completion_delete_after_seconds": MESSAGE_DELAYS[key]})

    async def _receive_text(
        self,
        message: Message,
        data: dict[str, Any],
        *,
        key: str,
        label: str,
        limit: int,
    ) -> None:
        changes = validated_text_changes(message.text or "", key=key, label=label, limit=limit)
        await self._dashboard_from_message(message, data | changes)

    async def _update(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        await self._dashboard(query, await self._required_data(query.from_user.id) | changes)

    async def _dashboard(self, query: CallbackQuery, data: dict[str, Any]) -> None:
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        text, rows = current_draft_view(data)
        await self._render(query, data=data, text=text, rows=rows)

    async def _dashboard_from_message(self, message: Message, data: dict[str, Any]) -> None:
        user_id = _user_id(message)
        await self._sessions.set(user_id, "draft:idle", data)
        text, rows = current_draft_view(data)
        await self._renderer.edit(_target(message, data), text=text, rows=rows)

    async def _set_state(self, query: CallbackQuery, state: str) -> dict[str, Any]:
        data = await self._required_data(query.from_user.id)
        updated = data | {"panel_message_id": _message(query).message_id, "guided": False}
        await self._sessions.set(query.from_user.id, state, updated)
        return updated

    async def _required_data(self, user_id: int) -> dict[str, Any]:
        from tg_lottery.telegram.creator_reuse import without_old_platform
        from tg_lottery.telegram.platform_requirements import attach_platform_policy

        return attach_platform_policy(
            without_old_platform(await required_draft_data(self._sessions, user_id)),
            self._platform_policy,
        )

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
