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
from tg_lottery.telegram.creator_behavior_admission import CreatorBehaviorAdmission
from tg_lottery.telegram.creator_folder_link import CreatorFolderLink
from tg_lottery.telegram.creator_group_picker import CreatorGroupPicker
from tg_lottery.telegram.creator_group_selection import (
    bind_initial_group,
    selected_chat_ids,
)
from tg_lottery.telegram.creator_input_replay import recover_applied_input
from tg_lottery.telegram.creator_message_context import callback_message as _message
from tg_lottery.telegram.creator_message_context import message_user as _user
from tg_lottery.telegram.creator_message_context import panel_target as _target
from tg_lottery.telegram.creator_prizes import PRIZE_ACTIONS, CreatorPrizes
from tg_lottery.telegram.creator_publication import CreatorPublisher
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer, PanelTarget
from tg_lottery.telegram.creator_sections import (
    SECTION_ACTIONS,
    current_section,
    enter_section,
    section_view,
)
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.creator_suspension import CreatorSuspension, resume_view, suspend_draft
from tg_lottery.telegram.creator_views import (
    dashboard_rows,
    preview_text,
    title_prompt_text,
)
from tg_lottery.telegram.draft_config import ensure_draft_identity, new_draft
from tg_lottery.telegram.home_views import home_keyboard, home_text
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
        self._suspension = CreatorSuspension(sessions=sessions, renderer=renderer)
        self._prizes = CreatorPrizes(sessions=sessions, renderer=renderer)
        self._behavior_admission = CreatorBehaviorAdmission(
            sessions=sessions, renderer=renderer, bot=bot
        )
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

    async def suspend(self, user_id: int) -> None:
        await suspend_draft(self._sessions, user_id)

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
        campaign = await self._recover_campaign(user.id, data)
        if campaign is not None:
            panel = await message.answer("正在恢复原活动的发布状态。")
            await self._publisher.resume_message(
                panel, user_id=user.id, data=data, campaign=campaign
            )
            return
        if existing is not None and existing.state != "draft:idle":
            data = data | {"suspended_state": data.get("suspended_state", existing.state)}
            panel = await message.answer("正在恢复草稿。")
            await self._show_resume(user.id, panel, data)
            return
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
        if existing.state == "draft:suspended":
            await self._show_resume(query.from_user.id, message, data)
        else:
            await self._render_dashboard(query.from_user.id, message, data)
        return True

    async def _open(self, user: User, message: Message) -> None:
        existing = await self._sessions.get(user.id)
        if existing is not None and existing.state.startswith("draft:"):
            data = ensure_draft_identity(dict(existing.data)) | {
                "panel_message_id": message.message_id,
                "creator_name": user.full_name,
            }
            campaign = await self._recover_campaign(user.id, data)
            if campaign is not None:
                await self._publisher.resume_message(
                    message, user_id=user.id, data=data, campaign=campaign
                )
                return
            if existing.state != "draft:idle":
                data = data | {"suspended_state": data.get("suspended_state", existing.state)}
                await self._show_resume(user.id, message, data)
                return
            await self._sessions.set(user.id, "draft:idle", data)
            await self._render_dashboard(user.id, message, data)
            return
        data = await self._new_draft(user, message.message_id)
        await self._render_dashboard(user.id, message, data)

    async def _show_resume(self, user_id, message, data):
        updated = data | {"panel_message_id": message.message_id}
        await self._sessions.set(user_id, "draft:suspended", updated)
        text, rows = resume_view(updated)
        await self._renderer.render(message, user_id=user_id, text=text, rows=rows)

    async def _recover_campaign(self, user_id: int, data: dict):
        campaign = await self._queries.campaign_for_draft(user_id, str(data["draft_id"]))
        if data.get("published_campaign_id") and campaign is None:
            raise UserFacingError(
                "原发布活动的身份暂未核实，请从我发起的抽奖核查，不能重新创建同一场"
            )
        return campaign

    async def start_new(self, query: CallbackQuery) -> None:
        message = _message(query)
        if message.chat.type != "private" or message.chat.id != query.from_user.id:
            raise UserFacingError("只能在本人私聊中创建新抽奖")
        existing = await self._sessions.get(query.from_user.id)
        if existing is not None and existing.state.startswith("draft:"):
            data = ensure_draft_identity(dict(existing.data))
            campaign = await self._recover_campaign(query.from_user.id, data)
            if campaign is None:
                await self._confirm_clear(query, None)
                return
        data = await self._new_draft(query.from_user, message.message_id)
        await self._render_dashboard(query.from_user.id, message, data)

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
        if await recover_applied_input(self._renderer, message, session):
            return True
        return await self.handle_text(message, session.state, dict(session.data))

    async def handle_text(self, message: Message, state: str, data: dict[str, Any]) -> bool:
        if state == "draft:group_search":
            await self._group_picker.receive_search(message, data)
            return True
        if state == "draft:suspended":
            return False
        if state in {"draft:prize_name", "draft:prize_count", "draft:prize_delete"}:
            await self._prizes.receive(message, state=state, data=data)
            return True
        if state == "draft:behavior_admission":
            await self._behavior_admission.receive(message, data)
            return True
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
        if grant.action.startswith("draft_content_page:"):
            await self._renderer.page(query, grant)
            return
        if grant.action in PRIZE_ACTIONS:
            await self._prizes.handle(query, grant)
            return
        if grant.action in SECTION_ACTIONS:
            await self._show_section(query, SECTION_ACTIONS[grant.action])
            return
        if grant.action == "draft_gate:choose_groups":
            await self._group_picker.show(query, grant)
            return
        if grant.action.startswith("draft_gate:"):
            closed = await self._behavior_admission.handle(query, grant)
            if closed:
                await self._show_dashboard(query, grant)
            return
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
            action in PRIZE_ACTIONS
            or action in SECTION_ACTIONS
            or action.startswith(("draft_base:", "draft_gate:", "draft_content_page:"))
            or action in self._grant_handlers()
            or self._settings.handles_action(action)
        )

    def _grant_handlers(self):
        return {
            "draft_resume_input": self._suspension.resume,
            "draft_resume_module": self._suspension.resume,
            "draft_gate": self._behavior_admission.show,
            "draft_base_activity": self._base_activity.show,
            "draft_folder_link": self._folder_link.show,
            "draft_folder_clear": self._folder_link.clear,
            "draft_groups_search": self._group_picker.search,
            "draft_groups_search_clear": self._group_picker.search,
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

    async def _receive_title(self, message: Message, data: dict[str, Any]) -> None:
        title = (message.text or "").strip()
        if not 1 <= len(title) <= 80:
            raise UserFacingError("抽奖标题长度必须为 1–80 字")
        updated = data | {"title": title}
        await self._sessions.set(_user(message).id, "draft:idle", updated)
        await self._render_dashboard_from_text(message, updated | {"guided": False})

    async def _show_dashboard(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        session = await self._sessions.get(query.from_user.id)
        if session is None:
            raise UserFacingError("创建草稿已过期，请发送/new重新创建")
        data = dict(session.data)
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
        path = list(data.get("creator_path") or ["dashboard"])
        input_states = {
            "draft:regions",
            "draft:description",
            "draft:issuer",
            "draft:claim",
            "draft:cover",
        }
        if grant.action == "draft_back" and session.state not in input_states and len(path) > 1:
            path = path[:-1]
        data = data | {"creator_path": path}
        if current_section(data) != "dashboard":
            await self._show_section(query, current_section(data), data=data)
            return
        await self._render_dashboard(query.from_user.id, _message(query), data)

    async def _show_section(self, query, section: str, *, data: dict | None = None) -> None:
        current = data if data is not None else await self._required_session(query.from_user.id)
        updated = enter_section(current, section) | {"panel_message_id": _message(query).message_id}
        text, rows = section_view(updated, section)
        await self._sessions.set(query.from_user.id, "draft:idle", updated)
        await self._renderer.render(
            _message(query), user_id=query.from_user.id, text=text, rows=rows
        )

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
        data = await self._required_session(query.from_user.id)
        await suspend_draft(self._sessions, query.from_user.id)
        await _message(query).edit_text(home_text(data), reply_markup=home_keyboard(data))

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

    async def _render_groups_from_text(self, message: Message, data: dict[str, Any]) -> None:
        await self._renderer.edit_groups(
            _target(message, data),
            selected_chat_ids=selected_chat_ids(data),
            page=int(data.get("group_page", 0)),
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


def _back_rows() -> list[list[tuple[str, str]]]:
    return [[("↩ 返回上一层", "draft_back")]]
