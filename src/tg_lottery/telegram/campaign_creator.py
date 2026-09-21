from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import (
    CallbackGrant,
)
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_group_bonus as group_bonus
from tg_lottery.telegram.creator_base_activity import CreatorBaseActivity
from tg_lottery.telegram.creator_folder_link import CreatorFolderLink
from tg_lottery.telegram.creator_group_picker import CreatorGroupPicker
from tg_lottery.telegram.creator_group_selection import (
    bind_initial_group,
    selected_chat_ids,
)
from tg_lottery.telegram.creator_participation import parse_positions
from tg_lottery.telegram.creator_publication import CreatorPublisher
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
from tg_lottery.telegram.draft_config import ensure_draft_identity, new_draft
from tg_lottery.telegram.issuer import creator_issuer


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
        self._sessions = sessions
        self._queries = queries
        self._renderer = renderer
        self._settings = settings
        self._base_activity = CreatorBaseActivity(sessions=sessions, renderer=renderer, bot=bot)
        self._folder_link = CreatorFolderLink(sessions=sessions, renderer=renderer)
        self._group_picker = CreatorGroupPicker(
            bot=bot,
            sessions=sessions,
            queries=queries,
            renderer=renderer,
        )
        self._publisher = CreatorPublisher(
            bot=bot,
            bot_username=bot_username,
            sessions=sessions,
            campaigns=campaigns,
            queries=queries,
            profiles=profiles,
            renderer=renderer,
        )

    async def start(self, query: CallbackQuery) -> None:
        await self._open(query.from_user, _message(query))

    async def start_message(self, message: Message) -> None:
        user = _user(message)
        existing = await self._sessions.get(user.id)
        data = (
            ensure_draft_identity(dict(existing.data))
            if existing is not None and existing.state.startswith("draft:")
            else await self._new_draft(user, message.message_id)
        ) | {"creator_name": user.full_name, "guided": False}
        panel = await message.answer(preview_text(data))
        updated = data | {"panel_message_id": panel.message_id}
        await self._sessions.set(user.id, "draft:idle", updated)
        keyboard = await self._renderer.keyboard_for(
            PanelTarget(user.id, panel.chat.id, panel.message_id), dashboard_rows(updated)
        )
        await panel.edit_reply_markup(reply_markup=keyboard)

    async def recover_expired_panel(self, query: CallbackQuery) -> bool:
        message = _message(query)
        if message.chat.type != "private" or message.chat.id != query.from_user.id:
            return False
        existing = await self._sessions.get(query.from_user.id)
        if existing is None or not existing.state.startswith("draft:"):
            return False
        if existing.data.get("panel_message_id") != message.message_id:
            await message.answer(
                "草稿已在另一条消息中更新，本次旧按钮操作未执行。\n"
                "已填写的内容仍然保留，点击下方按钮继续当前草稿。",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="继续当前草稿", callback_data="nav:create")]
                    ]
                ),
            )
            return True
        data = ensure_draft_identity(dict(existing.data)) | {
            "panel_message_id": message.message_id,
            "creator_name": query.from_user.full_name,
        }
        await self._render_dashboard(query.from_user.id, message, data)
        return True

    async def _open(self, user: User, message: Message) -> None:
        existing = await self._sessions.get(user.id)
        if existing is not None and existing.state.startswith("draft:"):
            data = ensure_draft_identity(dict(existing.data)) | {
                "panel_message_id": message.message_id,
                "creator_name": user.full_name,
            }
            await self._sessions.set(user.id, "draft:idle", data)
            await self._render_dashboard(user.id, message, data)
            return
        data = await self._new_draft(user, message.message_id)
        await self._render_dashboard(user.id, message, data)

    async def _new_draft(self, user: User, panel_message_id: int) -> dict[str, Any]:
        groups = await self._queries.admin_groups(user.id)
        data = new_draft()
        if len(groups) == 1:
            data = bind_initial_group(data, groups[0])
        return (
            data
            | {
                "panel_message_id": panel_message_id,
                "creator_name": user.full_name,
                "guided": False,
            }
            | creator_issuer(user)
        )

    async def handle_message(self, message: Message) -> bool:
        session = await self._sessions.get(_user(message).id)
        if session is None:
            return False
        return await self.handle_text(message, session.state, dict(session.data))

    async def handle_text(self, message: Message, state: str, data: dict[str, Any]) -> bool:
        if state == "draft:base_activity":
            await self._base_activity.receive(message, data)
            return True
        if state == "draft:folder_link":
            await self._folder_link.receive(message, data)
            return True
        if await self._settings.handle_message(message, state, data):
            return True
        handlers = {
            "draft:title": self._receive_title,
            "draft:prize_name": self._receive_prize_name,
            "draft:prize_count": self._receive_prize_count,
            "draft:prize_delete": self._receive_prize_delete,
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
        if grant.action.startswith("draft_base:"):
            await self._base_activity.handle(query, grant)
            return
        handler = self._grant_handlers().get(grant.action)
        if handler is not None:
            await handler(query, grant)
            return
        if not await self._settings.handle_grant(query, grant):
            raise UserFacingError("创建操作不存在或已经失效")

    def handles_action(self, action: str) -> bool:
        return (
            action.startswith("draft_base:")
            or action in self._grant_handlers()
            or self._settings.handles_action(action)
        )

    def _grant_handlers(self):
        return {
            "draft_base_activity": self._base_activity.show,
            "draft_folder_link": self._folder_link.show,
            "draft_folder_clear": self._folder_link.clear,
            "draft_group": self._group_picker.show,
            "draft_group_problem": self._group_picker.problem,
            "draft_toggle_group": self._group_picker.toggle,
            "draft_groups_select_page": self._group_picker.select_page,
            "draft_groups_clear_page": self._group_picker.clear_page,
            "draft_group_remove_confirm": self._group_picker.confirm_remove,
            "draft_group_remove_cancel": self._group_picker.cancel_remove,
            "draft_groups_prev": self._group_picker.page,
            "draft_groups_next": self._group_picker.page,
            "draft_groups_refresh": self._group_picker.page,
            "draft_groups_done": self._group_picker.done,
            "draft_title": self._edit_title,
            "draft_prizes": self._show_prize_prompt,
            "draft_add_prize": self._show_prize_prompt,
            "draft_delete_prize": self._delete_prize,
            "draft_clear_prizes": self._clear_prizes,
            "draft_back": self._show_dashboard,
            "draft_clear": self._confirm_clear,
            "draft_clear_confirm": self._clear,
            "draft_home": self._home,
            "draft_publish": self._publisher.confirm,
            "draft_publish_confirm": self._publisher.publish,
            "draft_publish_rules": self._publisher.show_rules,
            "draft_publish_rules_prev": self._publisher.show_rules,
            "draft_publish_rules_next": self._publisher.show_rules,
        }

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
        rows = _back_rows()
        if data.get("prizes"):
            rows = [
                [("删除指定奖品", "draft_delete_prize"), ("清空奖品", "draft_clear_prizes")],
                *rows,
            ]
        await self._render_text(
            query.from_user.id,
            _message(query),
            data=data,
            text=prize_prompt_text(data),
            rows=rows,
        )

    async def _receive_prize_name(self, message: Message, data: dict[str, Any]) -> None:
        name = (message.text or "").strip()
        if not 1 <= len(name) <= 120:
            raise UserFacingError("奖品名称长度必须为 1–120 字")
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
            raise UserFacingError("抽奖标题长度必须为 1–80 字")
        updated = data | {"title": title}
        await self._sessions.set(_user(message).id, "draft:idle", updated)
        if updated.get("guided") and not updated.get("draw_at"):
            await self._render_draw_from_text(message, updated)
            return
        await self._render_dashboard_from_text(message, updated | {"guided": False})

    async def _delete_prize(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:prize_delete", guided=False)
        lines = ["🎁 删除指定奖品", ""]
        lines.extend(
            f"{index}. {prize['name']} × {prize['winner_count']} 人"
            for index, prize in enumerate(data.get("prizes", []), start=1)
        )
        lines.append("\n请回复要删除的编号，多个编号用逗号分隔。")
        await self._render_text(
            query.from_user.id, _message(query), data=data, text="\n".join(lines), rows=_back_rows()
        )

    async def _receive_prize_delete(self, message: Message, data: dict[str, Any]) -> None:
        prizes = data.get("prizes", [])
        positions = parse_positions(message.text or "", len(prizes))
        updated = data | {
            "prizes": [
                prize for index, prize in enumerate(prizes, start=1) if index not in positions
            ]
        }
        await self._sessions.set(_user(message).id, "draft:idle", updated)
        await self._render_prizes_from_text(message, updated)

    async def _clear_prizes(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._update_and_prizes(query, {"prizes": []})

    async def _show_dashboard(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle", guided=False)
        if group_bonus.working_copy_dirty(data):
            rows = [
                [("放弃修改", "draft_bonus_group_cancel")],
                [("继续编辑", "draft_bonus_continue_editing")],
            ]
            await self._render_text(
                query.from_user.id,
                _message(query),
                data=data,
                text="当前群的加成设置尚未保存。要放弃修改还是继续编辑？",
                rows=rows,
            )
            return
        if data.get("bonus_working_copy"):
            data = group_bonus.apply_action(data, "draft_bonus_group_cancel")
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
        data = await self._new_draft(query.from_user, _message(query).message_id)
        await self._render_dashboard(query.from_user.id, _message(query), data)

    async def _home(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle", guided=False)
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await _message(query).edit_text("请选择操作：", reply_markup=main_keyboard())

    async def _render_dashboard(self, user_id: int, message: Message, data: dict[str, Any]) -> None:
        updated = data | {"guided": False}
        await self._sessions.set(user_id, "draft:idle", updated)
        await self._render_text(
            user_id,
            message,
            data=updated,
            text=preview_text(updated),
            rows=dashboard_rows(updated),
        )

    async def _render_prizes_from_text(self, message: Message, data: dict[str, Any]) -> None:
        rows = [
            [("继续添加", "draft_add_prize")],
            [("删除指定奖品", "draft_delete_prize"), ("清空奖品", "draft_clear_prizes")],
            *_back_rows(),
        ]
        await self._edit_from_text(message, data=data, text=prizes_text(data), rows=rows)

    async def _render_groups_from_text(self, message: Message, data: dict[str, Any]) -> None:
        await self._renderer.edit_groups(
            _target(message, data),
            selected_chat_ids=selected_chat_ids(data),
            page=int(data.get("group_page", 0)),
        )

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
            rows=[[("定时开奖", "draft_draw_fixed")]],
        )

    async def _render_dashboard_from_text(self, message: Message, data: dict[str, Any]) -> None:
        updated = data | {"guided": False}
        await self._sessions.set(_user(message).id, "draft:idle", updated)
        await self._edit_from_text(
            message, data=updated, text=preview_text(updated), rows=dashboard_rows(updated)
        )

    async def _update_and_dashboard(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        data = await self._required_session(query.from_user.id)
        await self._render_dashboard(query.from_user.id, _message(query), data | changes)

    async def _update_and_prizes(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        data = await self._required_session(query.from_user.id) | changes
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        rows = [
            [("继续添加", "draft_add_prize")],
            [("删除指定奖品", "draft_delete_prize"), ("清空奖品", "draft_clear_prizes")],
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
            raise UserFacingError("创建草稿已过期，请发送 /new 重新创建")
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


def _back_rows() -> list[list[tuple[str, str]]]:
    return [[("↩ 返回上一层", "draft_back")]]
