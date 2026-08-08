from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import (
    CallbackGrant,
    CallbackRequest,
    CallbackTokenService,
)
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.telegram.admin_authorization import verify_chat_admin
from tg_lottery.telegram.draft_config import (
    ACTIVITY_PRESETS,
    INVITE_PRESETS,
    LOCAL_TIME_FORMAT,
    campaign_input,
    new_draft,
    panel_text,
    parse_draw_at,
)

CREATOR_ACTIONS = frozenset(
    {
        "select_group",
        "draft_title",
        "draft_draw",
        "draft_prizes",
        "draft_claim",
        "draft_activity",
        "draft_invite",
        "draft_channel",
        "draft_back",
        "draft_add_prize",
        "draft_delete_prize",
        "draft_clear_prizes",
        "draft_activity_standard",
        "draft_activity_active",
        "draft_invite_standard",
        "draft_invite_strict",
        "draft_channel_clear",
        "draft_clear",
        "draft_cancel",
        "draft_publish",
    }
)
CREATOR_TOKEN_LIFETIME = timedelta(hours=24)


class CampaignCreator:
    def __init__(
        self,
        *,
        bot: Bot,
        bot_username: str,
        sessions: AdminSessionService,
        tokens: CallbackTokenService,
        campaigns: CampaignService,
        queries: QueryService,
    ) -> None:
        self._bot = bot
        self._bot_username = bot_username
        self._sessions = sessions
        self._tokens = tokens
        self._campaigns = campaigns
        self._queries = queries

    async def start(self, query: CallbackQuery) -> None:
        message = _message(query)
        groups = await self._queries.admin_groups(query.from_user.id)
        if not groups:
            await message.answer("没有可管理的群。请先将机器人设为群管理员。")
            return
        requests = tuple(
            CallbackRequest("select_group", payload={"chat_id": group.chat_id}) for group in groups
        )
        tokens = await self._create_tokens(message, query.from_user.id, requests)
        rows = [
            [InlineKeyboardButton(text=group.title, callback_data=f"secure:{token}")]
            for group, token in zip(groups, tokens, strict=True)
        ]
        await message.edit_text(
            "选择活动群：", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

    async def handle_text(self, message: Message, state: str, data: dict[str, Any]) -> bool:
        handlers = {
            "draft:title": self._receive_title,
            "draft:draw": self._receive_draw,
            "draft:prize_name": self._receive_prize_name,
            "draft:prize_count": self._receive_prize_count,
            "draft:claim": self._receive_claim,
            "draft:channel": self._receive_channel,
        }
        handler = handlers.get(state)
        if handler is None:
            if not state.startswith("draft:"):
                return False
            await message.answer("当前正在创建抽奖，请点击上方配置面板继续操作。")
            return True
        await handler(message, data)
        return True

    async def handle_message(self, message: Message) -> bool:
        session = await self._sessions.get(message.from_user.id)
        if session is None:
            return False
        return await self.handle_text(message, session.state, dict(session.data))

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        handlers = {
            "select_group": self._select_group,
            "draft_title": self._edit_title,
            "draft_draw": self._edit_draw,
            "draft_prizes": self._show_prizes,
            "draft_claim": self._edit_claim,
            "draft_activity": self._show_activity,
            "draft_invite": self._show_invite,
            "draft_channel": self._edit_channel,
            "draft_back": self._show_dashboard,
            "draft_add_prize": self._add_prize,
            "draft_delete_prize": self._delete_prize,
            "draft_clear_prizes": self._clear_prizes,
            "draft_activity_standard": self._set_activity,
            "draft_activity_active": self._set_activity,
            "draft_invite_standard": self._set_invite,
            "draft_invite_strict": self._set_invite,
            "draft_channel_clear": self._clear_channel,
            "draft_clear": self._clear,
            "draft_cancel": self._cancel,
            "draft_publish": self._publish,
        }
        await handlers[grant.action](query, grant)

    async def _select_group(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        chat_id = int(grant.payload["chat_id"])
        await verify_chat_admin(self._bot, chat_id, query.from_user.id)
        group = await self._queries.group_by_chat_id(chat_id)
        if group is None:
            raise ValueError("活动群不存在")
        data = new_draft(chat_id=chat_id, group_title=group.title, timezone=group.timezone)
        data["panel_message_id"] = _message(query).message_id
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await self._render_dashboard(query.from_user.id, _message(query), data)

    async def _edit_title(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:title")
        await self._edit_panel(query, "📝 设置活动标题\n\n请输入标题（最多 80 字）：")

    async def _edit_draw(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:draw")
        await self._edit_panel(
            query,
            "🌀 设置开奖时间\n\n"
            f"请输入群组本地时间：\n{LOCAL_TIME_FORMAT}\n\n"
            "示例：2026-08-08 18:30",
        )

    async def _show_prizes(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        await self._render_prizes(query.from_user.id, _message(query), data)

    async def _add_prize(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:prize_name")
        await self._edit_panel(query, "🎁 添加奖品\n\n请输入奖品名称（最多 120 字）：")

    async def _edit_claim(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:claim")
        await self._edit_panel(query, "⚠️ 设置领奖说明\n\n请输入中奖后的领取方式：")

    async def _edit_channel(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:channel")
        keyboard = await self._keyboard(
            query.from_user.id,
            _message(query),
            [[("不限制", "draft_channel_clear")], [("↩ 返回", "draft_back")]],
        )
        await _message(query).edit_text(
            "📣 设置必须关注的频道\n\n请输入频道数字 ID：", reply_markup=keyboard
        )

    async def _show_activity(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:idle")
        keyboard = await self._keyboard(
            query.from_user.id,
            _message(query),
            [
                [("标准：10条/1票/上限1", "draft_activity_standard")],
                [("高活跃：5条/1票/上限2", "draft_activity_active")],
                [("↩ 返回", "draft_back")],
            ],
        )
        await _message(query).edit_text(
            "🔥 选择活跃票规则\n\n每天按群消息达标发票：", reply_markup=keyboard
        )

    async def _show_invite(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._set_state(query, "draft:idle")
        keyboard = await self._keyboard(
            query.from_user.id,
            _message(query),
            [
                [("标准：留存24小时", "draft_invite_standard")],
                [("严格：留存48小时", "draft_invite_strict")],
                [("↩ 返回", "draft_back")],
            ],
        )
        await _message(query).edit_text(
            "👫 选择邀请票规则\n\n每个有效邀请奖励 1 票：", reply_markup=keyboard
        )

    async def _show_dashboard(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._set_state(query, "draft:idle")
        await self._render_dashboard(query.from_user.id, _message(query), data)

    async def _receive_title(self, message: Message, data: dict[str, Any]) -> None:
        title = (message.text or "").strip()
        if not 1 <= len(title) <= 80:
            raise ValueError("活动标题长度必须为 1–80 字")
        await self._save_and_dashboard(message, data | {"title": title})

    async def _receive_draw(self, message: Message, data: dict[str, Any]) -> None:
        draw_at = parse_draw_at(message.text or "", str(data["timezone"]))
        if draw_at.timestamp() <= utcnow().timestamp() + 300:
            raise ValueError("开奖时间至少需要晚于当前时间 5 分钟")
        await self._save_and_dashboard(message, data | {"draw_at": draw_at.isoformat()})

    async def _receive_prize_name(self, message: Message, data: dict[str, Any]) -> None:
        name = (message.text or "").strip()
        if not 1 <= len(name) <= 120:
            raise ValueError("奖品名称长度必须为 1–120 字")
        changes = data | {"pending_prize_name": name}
        await self._sessions.set(message.from_user.id, "draft:prize_count", changes)
        await self._edit_from_text(message, "🎁 设置中奖人数\n\n请输入正整数：", changes)

    async def _receive_prize_count(self, message: Message, data: dict[str, Any]) -> None:
        try:
            count = int((message.text or "").strip())
        except ValueError as exc:
            raise ValueError("中奖人数必须是正整数") from exc
        if count < 1:
            raise ValueError("中奖人数必须是正整数")
        prize = {"name": str(data["pending_prize_name"]), "winner_count": count}
        changes = data | {"prizes": [*data.get("prizes", []), prize]}
        changes.pop("pending_prize_name", None)
        await self._sessions.set(message.from_user.id, "draft:idle", changes)
        await self._render_prizes_from_text(message, changes)

    async def _receive_claim(self, message: Message, data: dict[str, Any]) -> None:
        instructions = (message.text or "").strip()
        if not 1 <= len(instructions) <= 500:
            raise ValueError("领奖说明长度必须为 1–500 字")
        await self._save_and_dashboard(message, data | {"claim_instructions": instructions})

    async def _receive_channel(self, message: Message, data: dict[str, Any]) -> None:
        try:
            channel_id = int((message.text or "").strip())
        except ValueError as exc:
            raise ValueError("频道 ID 必须是数字") from exc
        if channel_id == 0:
            raise ValueError("频道 ID 不能为 0；不限制请点击按钮")
        await self._save_and_dashboard(message, data | {"required_channel_id": channel_id})

    async def _set_activity(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        preset = "active" if grant.action.endswith("active") else "standard"
        if preset not in ACTIVITY_PRESETS:
            raise ValueError("未知活跃票规则")
        await self._update_and_dashboard(query, {"activity_preset": preset})

    async def _set_invite(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        preset = "strict" if grant.action.endswith("strict") else "standard"
        if preset not in INVITE_PRESETS:
            raise ValueError("未知邀请票规则")
        await self._update_and_dashboard(query, {"invite_preset": preset})

    async def _delete_prize(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_session(query.from_user.id)
        prizes = list(data.get("prizes", []))
        if prizes:
            prizes.pop()
        updated = data | {"prizes": prizes}
        await self._sessions.set(query.from_user.id, "draft:idle", updated)
        await self._render_prizes(query.from_user.id, _message(query), updated)

    async def _clear_prizes(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._update_and_prizes(query, {"prizes": []})

    async def _clear_channel(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._update_and_dashboard(query, {"required_channel_id": None})

    async def _clear(self, query: CallbackQuery, _: CallbackGrant) -> None:
        current = await self._required_session(query.from_user.id)
        data = new_draft(
            chat_id=int(current["chat_id"]),
            group_title=str(current["group_title"]),
            timezone=str(current["timezone"]),
        )
        data["panel_message_id"] = _message(query).message_id
        await self._sessions.set(query.from_user.id, "draft:idle", data)
        await self._render_dashboard(query.from_user.id, _message(query), data)

    async def _cancel(self, query: CallbackQuery, _: CallbackGrant) -> None:
        await self._sessions.clear(query.from_user.id)
        await _message(query).edit_text("已取消本次创建。发送 /menu 可返回主菜单。")

    async def _publish(self, query: CallbackQuery, _: CallbackGrant) -> None:
        data = await self._required_session(query.from_user.id)
        await verify_chat_admin(
            self._bot, int(data["chat_id"]), query.from_user.id, require_invites=True
        )
        draft, prizes = campaign_input(data, now=utcnow())
        campaign = await self._campaigns.create_draft(draft, prizes, query.from_user.id)
        await self._campaigns.publish(campaign.id, query.from_user.id, self._bot_username)
        await self._sessions.clear(query.from_user.id)
        await _message(query).edit_text("🎉 发布请求已提交。群活动卡片确认送达后，抽奖才正式开始。")

    async def _render_dashboard(self, user_id: int, message: Message, data: dict[str, Any]) -> None:
        keyboard = await self._keyboard(user_id, message, _dashboard_rows())
        await message.edit_text(panel_text(data), reply_markup=keyboard)

    async def _render_prizes(self, user_id: int, message: Message, data: dict[str, Any]) -> None:
        rows = [
            [("添加奖品", "draft_add_prize")],
            [("删除最后一个", "draft_delete_prize"), ("清空奖品", "draft_clear_prizes")],
            [("↩ 返回配置", "draft_back")],
        ]
        await message.edit_text(
            _prizes_text(data), reply_markup=await self._keyboard(user_id, message, rows)
        )

    async def _save_and_dashboard(self, message: Message, data: dict[str, Any]) -> None:
        await self._sessions.set(message.from_user.id, "draft:idle", data)
        await self._render_dashboard_from_text(message, data)

    async def _update_and_dashboard(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        data = await self._required_session(query.from_user.id)
        updated = data | changes
        await self._sessions.set(query.from_user.id, "draft:idle", updated)
        await self._render_dashboard(query.from_user.id, _message(query), updated)

    async def _update_and_prizes(self, query: CallbackQuery, changes: dict[str, Any]) -> None:
        data = await self._required_session(query.from_user.id)
        updated = data | changes
        await self._sessions.set(query.from_user.id, "draft:idle", updated)
        await self._render_prizes(query.from_user.id, _message(query), updated)

    async def _set_state(self, query: CallbackQuery, state: str) -> dict[str, Any]:
        data = await self._required_session(query.from_user.id)
        data["panel_message_id"] = _message(query).message_id
        await self._sessions.set(query.from_user.id, state, data)
        return data

    async def _required_session(self, user_id: int) -> dict[str, Any]:
        session = await self._sessions.get(user_id)
        if session is None or not session.state.startswith("draft:"):
            raise ValueError("创建草稿已过期，请重新创建")
        return dict(session.data)

    async def _edit_panel(self, query: CallbackQuery, text: str) -> None:
        keyboard = await self._keyboard(
            query.from_user.id, _message(query), [[("↩ 返回配置", "draft_back")]]
        )
        await _message(query).edit_text(text, reply_markup=keyboard)

    async def _render_dashboard_from_text(self, message: Message, data: dict[str, Any]) -> None:
        keyboard = await self._keyboard(
            message.from_user.id,
            message,
            _dashboard_rows(),
        )
        await self._bot.edit_message_text(
            panel_text(data),
            chat_id=message.chat.id,
            message_id=int(data["panel_message_id"]),
            reply_markup=keyboard,
        )

    async def _render_prizes_from_text(self, message: Message, data: dict[str, Any]) -> None:
        rows = [
            [("添加奖品", "draft_add_prize")],
            [("删除最后一个", "draft_delete_prize"), ("清空奖品", "draft_clear_prizes")],
            [("↩ 返回配置", "draft_back")],
        ]
        keyboard = await self._keyboard(message.from_user.id, message, rows)
        await self._bot.edit_message_text(
            _prizes_text(data),
            chat_id=message.chat.id,
            message_id=int(data["panel_message_id"]),
            reply_markup=keyboard,
        )

    async def _edit_from_text(self, message: Message, text: str, data: dict[str, Any]) -> None:
        keyboard = await self._keyboard(
            message.from_user.id, message, [[("↩ 返回配置", "draft_back")]]
        )
        await self._bot.edit_message_text(
            text,
            chat_id=message.chat.id,
            message_id=int(data["panel_message_id"]),
            reply_markup=keyboard,
        )

    async def _keyboard(
        self, user_id: int, message: Message, rows: list[list[tuple[str, str]]]
    ) -> InlineKeyboardMarkup:
        requests = tuple(CallbackRequest(action) for row in rows for _, action in row)
        tokens = iter(await self._create_tokens(message, user_id, requests))
        buttons = [
            [
                InlineKeyboardButton(text=label, callback_data=f"secure:{next(tokens)}")
                for label, _ in row
            ]
            for row in rows
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _create_tokens(
        self, message: Message, user_id: int, requests: tuple[CallbackRequest, ...]
    ) -> tuple[str, ...]:
        return await self._tokens.create_many(
            owner_user_id=user_id,
            chat_id=message.chat.id,
            requests=requests,
            expires_in=CREATOR_TOKEN_LIFETIME,
        )


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message


def _dashboard_rows() -> list[list[tuple[str, str]]]:
    return [
        [("活动标题", "draft_title"), ("开奖时间", "draft_draw")],
        [("奖品设置", "draft_prizes"), ("领奖说明", "draft_claim")],
        [("活跃票", "draft_activity"), ("邀请票", "draft_invite")],
        [("关注频道", "draft_channel")],
        [("🎉 发布抽奖", "draft_publish")],
        [("清空配置", "draft_clear"), ("取消创建", "draft_cancel")],
    ]


def _prizes_text(data: dict[str, Any]) -> str:
    prizes = data.get("prizes", [])
    if not prizes:
        return "🎁 奖品设置\n\n暂未添加奖品"
    lines = [
        f"{index}. {item['name']} × {item['winner_count']} 人"
        for index, item in enumerate(prizes, start=1)
    ]
    return "🎁 奖品设置\n\n" + "\n".join(lines)
