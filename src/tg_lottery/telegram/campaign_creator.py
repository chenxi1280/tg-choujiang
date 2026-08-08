from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import (
    CallbackGrant,
)
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.infrastructure.db import Group
from tg_lottery.telegram.admin_authorization import verify_chat_admin
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer, PanelTarget
from tg_lottery.telegram.creator_schedule import positive_int
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.creator_views import (
    dashboard_rows,
    main_keyboard,
    preview_text,
    prize_count_text,
    prize_prompt_text,
    prizes_text,
    title_prompt_text,
)
from tg_lottery.telegram.draft_config import campaign_input, new_draft, presentation_from_data


class CampaignCreator:
    def __init__(
        self,
        *,
        bot: Bot,
        bot_username: str,
        sessions: AdminSessionService,
        campaigns: CampaignService,
        queries: QueryService,
        renderer: CreatorRenderer,
        settings: CreatorSettings,
        profiles: UserProfileService,
    ) -> None:
        self._bot = bot
        self._bot_username = bot_username
        self._sessions = sessions
        self._campaigns = campaigns
        self._queries = queries
        self._renderer = renderer
        self._settings = settings
        self._profiles = profiles

    async def start(self, query: CallbackQuery) -> None:
        await self._open(query.from_user.id, query.from_user.full_name, _message(query))

    async def start_message(self, message: Message) -> None:
        user = _user(message)
        panel = await message.answer("正在准备抽奖草稿…")
        await self._open(user.id, user.full_name, panel)

    async def _open(self, user_id: int, creator_name: str, message: Message) -> None:
        existing = await self._sessions.get(user_id)
        if existing is not None and existing.state.startswith("draft:"):
            data = dict(existing.data) | {
                "panel_message_id": message.message_id,
                "creator_name": creator_name,
            }
            await self._sessions.set(user_id, "draft:idle", data)
            await self._render_dashboard(user_id, message, data)
            return
        data = await self._new_draft(user_id, creator_name, message.message_id)
        await self._sessions.set(user_id, "draft:prize_name", data)
        await self._render_text(
            user_id, message, data=data, text=prize_prompt_text(data), rows=_back_rows()
        )

    async def _new_draft(
        self, user_id: int, creator_name: str, panel_message_id: int
    ) -> dict[str, Any]:
        groups = await self._queries.admin_groups(user_id)
        if not groups:
            raise ValueError("没有可管理的群，请先将机器人设为群管理员")
        data = new_draft()
        if len(groups) == 1:
            data = _bind_group(data, groups[0])
        return data | {
            "panel_message_id": panel_message_id,
            "creator_name": creator_name,
            "guided": True,
        }

    async def handle_message(self, message: Message) -> bool:
        session = await self._sessions.get(_user(message).id)
        if session is None:
            return False
        return await self.handle_text(message, session.state, dict(session.data))

    async def handle_text(self, message: Message, state: str, data: dict[str, Any]) -> bool:
        if await self._settings.handle_message(message, state, data):
            return True
        handlers = {
            "draft:title": self._receive_title,
            "draft:prize_name": self._receive_prize_name,
            "draft:prize_count": self._receive_prize_count,
        }
        handler = handlers.get(state)
        if handler is not None:
            await handler(message, data)
            return True
        if not state.startswith("draft:"):
            return False
        await message.answer("当前正在创建抽奖，请点击上方草稿中的按钮继续。")
        return True

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        handlers = {
            "draft_group": self._show_groups,
            "draft_select_group": self._select_group,
            "draft_title": self._edit_title,
            "draft_prizes": self._show_prize_prompt,
            "draft_add_prize": self._show_prize_prompt,
            "draft_delete_prize": self._delete_prize,
            "draft_clear_prizes": self._clear_prizes,
            "draft_back": self._show_dashboard,
            "draft_clear": self._confirm_clear,
            "draft_clear_confirm": self._clear,
            "draft_home": self._home,
            "draft_publish": self._publish,
        }
        handler = handlers.get(grant.action)
        if handler is not None:
            await handler(query, grant)
            return
        if not await self._settings.handle_grant(query, grant):
            raise ValueError("创建操作不存在或已经失效")

    async def _show_groups(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:idle", guided=False)
        await self._renderer.render_groups(_message(query), user_id=query.from_user.id)

    async def _select_group(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        chat_id = int(grant.payload["chat_id"])
        await verify_chat_admin(self._bot, chat_id, query.from_user.id)
        group = await self._queries.group_by_chat_id(chat_id)
        if group is None:
            raise ValueError("活动群不存在")
        data = _bind_group(await self._required_session(query.from_user.id), group)
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        if data.get("guided") and not data.get("title"):
            await self._prompt_title(query.from_user.id, _message(query), data)
            return
        await self._render_dashboard(query.from_user.id, _message(query), data)

    async def _edit_title(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:title", guided=False)
        await self._render_text(
            query.from_user.id,
            _message(query),
            data=data,
            text=title_prompt_text(data),
            rows=_back_rows(),
        )

    async def _show_prize_prompt(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:prize_name", guided=False)
        await self._render_text(
            query.from_user.id,
            _message(query),
            data=data,
            text=prize_prompt_text(data),
            rows=_back_rows(),
        )

    async def _receive_prize_name(self, message: Message, data: dict[str, Any]) -> None:
        name = (message.text or "").strip()
        if not 1 <= len(name) <= 120:
            raise ValueError("奖品名称长度必须为 1–120 字")
        updated = data | {"pending_prize_name": name}
        await self._sessions.set(_user(message).id, "draft:prize_count", updated)
        await self._edit_from_text(
            message, data=updated, text=prize_count_text(updated), rows=_back_rows()
        )

    async def _receive_prize_count(self, message: Message, data: dict[str, Any]) -> None:
        count = positive_int(message.text or "", "中奖人数")
        prize = {"name": str(data["pending_prize_name"]), "winner_count": count}
        updated = data | {"prizes": [*data.get("prizes", []), prize]}
        updated.pop("pending_prize_name", None)
        await self._sessions.set(_user(message).id, "draft:idle", updated)
        if updated.get("guided"):
            await self._advance_guided(message, updated)
            return
        await self._render_prizes_from_text(message, updated)

    async def _advance_guided(self, message: Message, data: dict[str, Any]) -> None:
        if data.get("chat_id") is None:
            await self._render_groups_from_text(message, data)
            return
        if not data.get("title"):
            await self._prompt_title_from_text(message, data)
            return
        if not data.get("draw_at"):
            await self._render_draw_from_text(message, data)
            return
        await self._render_dashboard_from_text(message, data | {"guided": False})

    async def _receive_title(self, message: Message, data: dict[str, Any]) -> None:
        title = (message.text or "").strip()
        if not 1 <= len(title) <= 80:
            raise ValueError("抽奖标题长度必须为 1–80 字")
        updated = data | {"title": title}
        await self._sessions.set(_user(message).id, "draft:idle", updated)
        if updated.get("guided") and not updated.get("draw_at"):
            await self._render_draw_from_text(message, updated)
            return
        await self._render_dashboard_from_text(message, updated | {"guided": False})

    async def _delete_prize(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_session(query.from_user.id)
        prizes = list(data.get("prizes", []))
        if prizes:
            prizes.pop()
        await self._update_and_prizes(query, {"prizes": prizes})

    async def _clear_prizes(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._update_and_prizes(query, {"prizes": []})

    async def _show_dashboard(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle", guided=False)
        await self._render_dashboard(query.from_user.id, _message(query), data)

    async def _confirm_clear(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle", guided=False)
        rows = [[("确认清空", "draft_clear_confirm")], *_back_rows()]
        await self._render_text(
            query.from_user.id,
            _message(query),
            data=data,
            text="🗑 确认清空当前抽奖草稿？\n\n清空后无法恢复。",
            rows=rows,
        )

    async def _clear(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._new_draft(
            query.from_user.id, query.from_user.full_name, _message(query).message_id
        )
        await self._sessions.set(query.from_user.id, "draft:prize_name", data)
        await self._render_text(
            query.from_user.id,
            _message(query),
            data=data,
            text=prize_prompt_text(data),
            rows=_back_rows(),
        )

    async def _home(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle", guided=False)
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await _message(query).edit_text("请选择操作：", reply_markup=main_keyboard())

    async def _publish(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_session(query.from_user.id)
        now = utcnow()
        campaign_input(data, now=now)
        await verify_chat_admin(
            self._bot, int(data["chat_id"]), query.from_user.id, require_invites=True
        )
        required_chats = await self._resolve_required_chats(data)
        resolved = data | {
            "required_chat_ids": [item["id"] for item in required_chats],
            "required_chats": required_chats,
        }
        draft, prizes = campaign_input(resolved, now=now)
        campaign = await self._campaigns.create_draft(draft, prizes, query.from_user.id)
        await self._campaigns.publish(campaign.id, query.from_user.id, self._bot_username)
        await self._profiles.save_last_draft(
            query.from_user.id, resolved | presentation_from_data(resolved), utcnow()
        )
        await self._sessions.clear(query.from_user.id)
        await _message(query).edit_text("🎉 发布请求已提交，群活动卡片确认送达后正式开始。")

    async def _resolve_required_chats(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        refs = data.get("required_chat_refs") or data.get("required_chat_ids", [])
        records: list[dict[str, Any]] = []
        seen: set[int] = {int(data["chat_id"])}
        for ref in refs:
            record = await self._required_chat_record(ref)
            chat_id = int(record["id"])
            if chat_id in seen:
                continue
            seen.add(chat_id)
            records.append(record)
        return records

    async def _required_chat_record(self, ref: int | str) -> dict[str, Any]:
        try:
            chat = await self._bot.get_chat(ref)
            member = await self._bot.get_chat_member(chat.id, self._bot.id)
        except TelegramAPIError as exc:
            raise ValueError(f"无法验证参与条件 {ref}: {exc}") from exc
        if str(member.status) not in {"administrator", "creator"}:
            raise ValueError(f"机器人必须是参与条件 {ref} 的管理员")
        return {
            "id": chat.id,
            "title": chat.title or chat.full_name or str(chat.id),
            "type": str(chat.type),
            "username": chat.username,
        }

    async def _prompt_title(self, user_id: int, message: Message, data: dict[str, Any]) -> None:
        await self._sessions.set(user_id, "draft:title", data)
        await self._render_text(
            user_id, message, data=data, text=title_prompt_text(data), rows=_back_rows()
        )

    async def _render_dashboard(self, user_id: int, message: Message, data: dict[str, Any]) -> None:
        updated = data | {"guided": False}
        await self._sessions.set(user_id, "draft:idle", updated)
        await self._render_text(
            user_id,
            message,
            data=updated,
            text=preview_text(updated),
            rows=dashboard_rows(),
        )

    async def _render_prizes_from_text(self, message: Message, data: dict[str, Any]) -> None:
        rows = [
            [("继续添加", "draft_add_prize")],
            [("删除最后一个", "draft_delete_prize"), ("清空奖品", "draft_clear_prizes")],
            *_back_rows(),
        ]
        await self._edit_from_text(message, data=data, text=prizes_text(data), rows=rows)

    async def _render_groups_from_text(self, message: Message, data: dict[str, Any]) -> None:
        await self._renderer.edit_groups(_target(message, data))

    async def _prompt_title_from_text(self, message: Message, data: dict[str, Any]) -> None:
        await self._sessions.set(_user(message).id, "draft:title", data)
        await self._edit_from_text(
            message, data=data, text=title_prompt_text(data), rows=_back_rows()
        )

    async def _render_draw_from_text(self, message: Message, data: dict[str, Any]) -> None:
        await self._sessions.set(_user(message).id, "draft:idle", data)
        await self._edit_from_text(
            message,
            data=data,
            text="🌀 请选择开奖方式：",
            rows=[[("满人开奖", "draft_draw_full"), ("定时开奖", "draft_draw_fixed")]],
        )

    async def _render_dashboard_from_text(self, message: Message, data: dict[str, Any]) -> None:
        updated = data | {"guided": False}
        await self._sessions.set(_user(message).id, "draft:idle", updated)
        await self._edit_from_text(
            message, data=updated, text=preview_text(updated), rows=dashboard_rows()
        )

    async def _update_and_dashboard(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        data = await self._required_session(query.from_user.id)
        await self._render_dashboard(query.from_user.id, _message(query), data | changes)

    async def _update_and_prizes(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        data = await self._required_session(query.from_user.id) | changes
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        rows = [
            [("继续添加", "draft_add_prize")],
            [("删除最后一个", "draft_delete_prize"), ("清空奖品", "draft_clear_prizes")],
            *_back_rows(),
        ]
        await self._render_text(
            query.from_user.id,
            _message(query),
            data=data,
            text=prizes_text(data),
            rows=rows,
        )

    async def _set_state(self, query: CallbackQuery, state: str, *, guided: bool) -> dict[str, Any]:
        data = await self._required_session(query.from_user.id)
        updated = data | {"panel_message_id": _message(query).message_id, "guided": guided}
        await self._sessions.set(query.from_user.id, state, updated)
        return updated

    async def _required_session(self, user_id: int) -> dict[str, Any]:
        session = await self._sessions.get(user_id)
        if session is None or not session.state.startswith("draft:"):
            raise ValueError("创建草稿已过期，请发送 /new 重新创建")
        return dict(session.data)

    async def _render_text(
        self,
        user_id: int,
        message: Message,
        *,
        data: dict[str, Any],
        text: str,
        rows: ButtonRows,
    ) -> None:
        await self._renderer.render(message, user_id=user_id, text=text, rows=rows)

    async def _edit_from_text(
        self,
        message: Message,
        *,
        data: dict[str, Any],
        text: str,
        rows: ButtonRows,
    ) -> None:
        await self._renderer.edit(_target(message, data), text=text, rows=rows)


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message


def _user(message: Message):
    if message.from_user is None:
        raise ValueError("无法识别当前用户")
    return message.from_user


def _target(message: Message, data: dict[str, Any]) -> PanelTarget:
    return PanelTarget(
        user_id=_user(message).id,
        chat_id=message.chat.id,
        message_id=int(data["panel_message_id"]),
    )


def _bind_group(data: dict[str, Any], group: Group) -> dict[str, Any]:
    return data | {
        "chat_id": group.chat_id,
        "group_title": group.title,
        "timezone": group.timezone,
    }


def _back_rows() -> list[list[tuple[str, str]]]:
    return [[("↩ 返回上一层", "draft_back")]]
