from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import (
    CallbackGrant,
    CallbackRequest,
    CallbackTokenService,
)
from tg_lottery.application.campaigns import CampaignService, PrizeSpec
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.winners import WinnerService
from tg_lottery.domain.enums import CampaignState, PrizeClaimState
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.domain.rules import (
    ActivityRule,
    CampaignDraft,
    CampaignRules,
    InviteRule,
)

_TIMELINE_FORMAT = "%Y-%m-%d %H:%M"


class AdminFlow:
    def __init__(
        self,
        *,
        bot: Bot,
        bot_username: str,
        sessions: AdminSessionService,
        tokens: CallbackTokenService,
        campaigns: CampaignService,
        drawing: DrawingService,
        winners: WinnerService,
        queries: QueryService,
    ) -> None:
        self._bot = bot
        self._bot_username = bot_username
        self._sessions = sessions
        self._tokens = tokens
        self._campaigns = campaigns
        self._drawing = drawing
        self._winners = winners
        self._queries = queries

    async def show_main(self, message: Message) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="创建抽奖", callback_data="nav:create")],
                [InlineKeyboardButton(text="我的活动", callback_data="nav:campaigns")],
                [InlineKeyboardButton(text="我的票数", callback_data="member:tickets")],
                [InlineKeyboardButton(text="邀请记录", callback_data="member:invites")],
            ]
        )
        await message.answer("请选择操作：", reply_markup=keyboard)

    async def start_create(self, query: CallbackQuery) -> None:
        message = _message(query)
        groups = await self._queries.admin_groups(query.from_user.id)
        if not groups:
            await message.answer("没有可管理的群。请先将机器人设为群管理员。")
            return
        requests = tuple(
            CallbackRequest("select_group", payload={"chat_id": group.chat_id}) for group in groups
        )
        token_values = await self._tokens.create_many(
            owner_user_id=query.from_user.id,
            chat_id=message.chat.id,
            requests=requests,
        )
        buttons = [
            [InlineKeyboardButton(text=group.title, callback_data=f"secure:{token}")]
            for group, token in zip(groups, token_values, strict=True)
        ]
        await message.answer(
            "选择活动群：",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    async def show_campaigns(self, query: CallbackQuery) -> None:
        message = _message(query)
        campaigns = await self._queries.admin_campaigns(query.from_user.id)
        if not campaigns:
            await message.answer("暂时没有活动。")
            return
        requests = tuple(
            CallbackRequest("view_campaign", campaign_id=campaign.id) for campaign in campaigns
        )
        token_values = await self._tokens.create_many(
            owner_user_id=query.from_user.id,
            chat_id=message.chat.id,
            requests=requests,
        )
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"{campaign.name} · {campaign.state.value}",
                    callback_data=f"secure:{token}",
                )
            ]
            for campaign, token in zip(campaigns, token_values, strict=True)
        ]
        await message.answer(
            "我的活动：",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    async def handle_text(self, message: Message) -> bool:
        session = await self._sessions.get(message.from_user.id)
        if session is None:
            return False
        handlers = {
            "campaign_name": self._receive_name,
            "timeline": self._receive_timeline,
            "prize": self._receive_prize,
            "rules": self._receive_rules,
        }
        handler = handlers.get(session.state)
        if handler is None:
            await message.answer("当前步骤需要点击上方按钮。")
            return True
        await handler(message, session.data)
        return True

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        handlers = {
            "select_group": self._select_group,
            "add_prize": self._add_prize,
            "finish_prizes": self._finish_prizes,
            "publish": self._publish,
            "view_campaign": self._view_campaign,
            "pause": self._pause,
            "resume": self._resume,
            "early_cutoff": self._early_cutoff,
            "draw": self._draw,
            "cancel": self._cancel,
            "mark_claimed": self._mark_claimed,
        }
        handler = handlers.get(grant.action)
        if handler is None:
            raise ValueError(f"unknown callback action: {grant.action}")
        await handler(query, grant)

    async def _select_group(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        chat_id = int(grant.payload["chat_id"])
        await self._verify_chat_admin(chat_id, query.from_user.id)
        await self._sessions.set(
            query.from_user.id,
            "campaign_name",
            {"chat_id": chat_id, "prizes": []},
        )
        await _message(query).answer("请输入活动名称（最多 120 字）：")

    async def _receive_name(self, message: Message, data: dict[str, Any]) -> None:
        name = (message.text or "").strip()
        if not 1 <= len(name) <= 120:
            raise ValueError("活动名称长度必须为 1–120 字")
        await self._sessions.update(message.from_user.id, "timeline", {"name": name})
        await message.answer(
            "请按格式输入预热、截止、开奖时间：\n"
            "2026-08-08 18:00 | 2026-08-10 18:00 | 2026-08-10 18:10"
        )

    async def _receive_timeline(self, message: Message, data: dict[str, Any]) -> None:
        group = await self._queries.group_by_chat_id(int(data["chat_id"]))
        if group is None:
            raise ValueError("活动群不存在")
        parts = [part.strip() for part in (message.text or "").split("|")]
        if len(parts) != 3:
            raise ValueError("时间必须由三个字段组成，并使用 | 分隔")
        zone = ZoneInfo(group.timezone)
        values = [_parse_local(value, zone) for value in parts]
        if values[1] <= utcnow():
            raise ValueError("截止时间必须晚于当前时间")
        changes = {
            "preheat_at": values[0].isoformat(),
            "cutoff_at": values[1].isoformat(),
            "draw_at": values[2].isoformat(),
        }
        await self._sessions.update(message.from_user.id, "prize", changes)
        await message.answer("请输入奖品：奖品名称 | 中奖人数 | 领取说明")

    async def _receive_prize(self, message: Message, data: dict[str, Any]) -> None:
        parts = [part.strip() for part in (message.text or "").split("|", 2)]
        if len(parts) != 3 or not parts[0] or not parts[2]:
            raise ValueError("奖品必须包含名称、中奖人数和领取说明")
        if len(parts[0]) > 120:
            raise ValueError("奖品名称最多 120 字")
        winner_count = int(parts[1])
        if winner_count < 1:
            raise ValueError("中奖人数必须大于 0")
        prizes = [*data.get("prizes", [])]
        prizes.append({"name": parts[0], "winner_count": winner_count, "instructions": parts[2]})
        await self._sessions.update(message.from_user.id, "prize_more", {"prizes": prizes})
        tokens = await self._tokens.create_many(
            owner_user_id=message.from_user.id,
            chat_id=message.chat.id,
            requests=(CallbackRequest("add_prize"), CallbackRequest("finish_prizes")),
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="继续添加奖品", callback_data=f"secure:{tokens[0]}")],
                [InlineKeyboardButton(text="奖品已完成", callback_data=f"secure:{tokens[1]}")],
            ]
        )
        await message.answer("奖品已记录。", reply_markup=keyboard)

    async def _add_prize(self, query: CallbackQuery, _: CallbackGrant) -> None:
        session = await self._sessions.get(query.from_user.id)
        if session is None or session.state != "prize_more":
            raise ValueError("创建会话已过期")
        await self._sessions.set(query.from_user.id, "prize", session.data)
        await _message(query).answer("请输入下一个奖品：奖品名称 | 中奖人数 | 领取说明")

    async def _finish_prizes(self, query: CallbackQuery, _: CallbackGrant) -> None:
        session = await self._sessions.get(query.from_user.id)
        if session is None or not session.data.get("prizes"):
            raise ValueError("至少需要一个奖品")
        await self._sessions.set(query.from_user.id, "rules", session.data)
        await _message(query).answer(
            "请输入规则：活跃阈值 | 活跃奖励票 | 每日票上限 | "
            "每个有效邀请奖励票 | 留存小时 | 必须关注频道ID（无则填 0）"
        )

    async def _receive_rules(self, message: Message, data: dict[str, Any]) -> None:
        parts = [part.strip() for part in (message.text or "").split("|")]
        if len(parts) != 6:
            raise ValueError("规则必须包含 6 个字段")
        threshold, activity_reward, cap, invite_reward, retention, channel_id = map(int, parts)
        rules = CampaignRules(
            timezone=(await self._required_group(data)).timezone,
            activity=ActivityRule(
                message_threshold=threshold,
                reward_tickets=activity_reward,
                daily_ticket_cap=cap,
            ),
            invite=InviteRule(reward_tickets=invite_reward, retention_hours=retention),
            required_channel_id=channel_id or None,
        )
        campaign = await self._create_draft(message, data, rules)
        await self._sessions.clear(message.from_user.id)
        await self._show_preview(message, campaign.id)

    async def _create_draft(self, message: Message, data: dict[str, Any], rules: CampaignRules):
        await self._verify_chat_admin(int(data["chat_id"]), message.from_user.id)
        draft = CampaignDraft(
            group_id=int(data["chat_id"]),
            name=str(data["name"]),
            preheat_at=datetime.fromisoformat(str(data["preheat_at"])),
            cutoff_at=datetime.fromisoformat(str(data["cutoff_at"])),
            draw_at=datetime.fromisoformat(str(data["draw_at"])),
            rules=rules,
        )
        prizes = tuple(
            PrizeSpec(str(item["name"]), int(item["winner_count"]), str(item["instructions"]))
            for item in data["prizes"]
        )
        return await self._campaigns.create_draft(draft, prizes, message.from_user.id)

    async def _show_preview(self, message: Message, campaign_id: UUID) -> None:
        campaign = await self._required_campaign(campaign_id)
        prizes = await self._queries.prizes(campaign.id)
        tokens = await self._tokens.create_many(
            owner_user_id=message.from_user.id,
            chat_id=message.chat.id,
            requests=(CallbackRequest("publish", campaign_id=campaign.id),),
        )
        prize_text = "\n".join(
            f"• 第{item.position}顺位 {item.name}：中奖 {item.winner_count} 人" for item in prizes
        )
        text = (
            f"活动预览\n名称: {campaign.name}\n截止: {campaign.cutoff_at}\n"
            f"开奖: {campaign.draw_at}\n奖品:\n{prize_text}"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="发布到群", callback_data=f"secure:{tokens[0]}")]
            ]
        )
        await message.answer(text, reply_markup=keyboard)

    async def _publish(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id, require_invites=True)
        await self._campaigns.publish(campaign_id, query.from_user.id, self._bot_username)
        await _message(query).answer("发布请求已入队；群卡片确认送达后活动才会进入运行状态。")

    async def _view_campaign(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._show_campaign(_message(query), campaign_id, query.from_user.id)

    async def _pause(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._state_action(
            query, grant=grant, target=CampaignState.PAUSED, reason="管理员暂停"
        )

    async def _resume(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._state_action(
            query, grant=grant, target=CampaignState.RUNNING, reason="管理员恢复"
        )

    async def _cancel(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._state_action(
            query, grant=grant, target=CampaignState.CANCELLED, reason="管理员取消"
        )

    async def _state_action(
        self,
        query: CallbackQuery,
        *,
        grant: CallbackGrant,
        target: CampaignState,
        reason: str,
    ) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._campaigns.change_state(
            campaign_id,
            actor_user_id=query.from_user.id,
            target=target,
            reason=reason,
        )
        await _message(query).answer(f"活动状态已更新为 {target.value}。")

    async def _early_cutoff(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._campaigns.early_cutoff(
            campaign_id,
            actor_user_id=query.from_user.id,
            cutoff_at=utcnow(),
            reason="管理员提前截止",
        )
        await _message(query).answer("活动已进入结算中；排空与资格复核完成后才可开奖。")

    async def _draw(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._drawing.run(campaign_id, query.from_user.id)
        await _message(query).answer("数据库中奖结果已生成；群公告和私聊通知正在独立送达。")

    async def _mark_claimed(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._verify_admin(_campaign_id(grant), query.from_user.id)
        winner_id = UUID(str(grant.payload["winner_id"]))
        await self._winners.mark_claimed(winner_id, query.from_user.id)
        await _message(query).answer("领奖状态已登记。")

    async def _show_campaign(self, message: Message, campaign_id: UUID, user_id: int) -> None:
        campaign = await self._required_campaign(campaign_id)
        stats = await self._queries.campaign_stats(campaign_id)
        deliveries = await self._queries.delivery_summary(campaign_id)
        actions = _campaign_actions(campaign.state)
        requests = tuple(CallbackRequest(action, campaign_id=campaign_id) for action in actions)
        labels = {
            "pause": "暂停",
            "resume": "恢复",
            "early_cutoff": "提前截止",
            "draw": "立即开奖",
            "cancel": "取消",
        }
        tokens = await self._tokens.create_many(
            owner_user_id=user_id,
            chat_id=message.chat.id,
            requests=requests,
        )
        buttons = [
            [InlineKeyboardButton(text=labels[action], callback_data=f"secure:{token}")]
            for action, token in zip(actions, tokens, strict=True)
        ]
        if campaign.state is CampaignState.DRAWN:
            buttons.extend(await self._claim_buttons(message, campaign_id, user_id))
        text = (
            f"{campaign.name}\n状态: {campaign.state.value}\n"
            f"报名: {stats.participants}\n有效邀请: {stats.valid_invitations}\n"
            f"待验证邀请: {stats.pending_invitations}\n总票数: {stats.tickets}\n"
            f"送达: 已确认 {deliveries.confirmed} / 待处理 {deliveries.pending} / "
            f"失败 {deliveries.failed} / 未知 {deliveries.unknown}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        await message.answer(text, reply_markup=keyboard)

    async def _claim_buttons(
        self, message: Message, campaign_id: UUID, user_id: int
    ) -> list[list[InlineKeyboardButton]]:
        rows = await self._queries.winners(campaign_id)
        pending = [row for row in rows if row.winner.claim_state is PrizeClaimState.PENDING]
        requests = tuple(
            CallbackRequest(
                "mark_claimed",
                campaign_id=campaign_id,
                payload={"winner_id": str(row.winner.id)},
            )
            for row in pending
        )
        tokens = await self._tokens.create_many(
            owner_user_id=user_id,
            chat_id=message.chat.id,
            requests=requests,
        )
        return [
            [
                InlineKeyboardButton(
                    text=f"登记领奖 · {row.participant.display_name} · {row.prize.name}",
                    callback_data=f"secure:{token}",
                )
            ]
            for row, token in zip(pending, tokens, strict=True)
        ]

    async def _verify_admin(
        self, campaign_id: UUID, user_id: int, *, require_invites: bool = False
    ) -> None:
        group = await self._queries.campaign_group(campaign_id)
        await self._verify_chat_admin(group.chat_id, user_id, require_invites=require_invites)

    async def _verify_chat_admin(
        self, chat_id: int, user_id: int, *, require_invites: bool = False
    ) -> None:
        user = await self._bot.get_chat_member(chat_id, user_id)
        bot = await self._bot.get_chat_member(chat_id, self._bot.id)
        if not _is_admin(user) or not _is_admin(bot):
            raise AuthorizationDenied("当前 Telegram 管理员身份无效")
        if require_invites and not bool(getattr(bot, "can_invite_users", False)):
            raise AuthorizationDenied("机器人缺少管理邀请链接权限")

    async def _required_group(self, data: dict[str, Any]):
        group = await self._queries.group_by_chat_id(int(data["chat_id"]))
        if group is None:
            raise ValueError("活动群不存在")
        return group

    async def _required_campaign(self, campaign_id: UUID):
        campaign = await self._queries.campaign(campaign_id)
        if campaign is None:
            raise ValueError("活动不存在")
        return campaign


def _parse_local(value: str, zone: ZoneInfo) -> datetime:
    return datetime.strptime(value, _TIMELINE_FORMAT).replace(tzinfo=zone)


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message


def _campaign_id(grant: CallbackGrant) -> UUID:
    if grant.campaign_id is None:
        raise ValueError("callback is missing campaign binding")
    return grant.campaign_id


def _is_admin(member: object) -> bool:
    return str(getattr(member, "status", "")) in {"administrator", "creator"}


def _campaign_actions(state: CampaignState) -> tuple[str, ...]:
    if state is CampaignState.RUNNING:
        return "pause", "early_cutoff", "cancel"
    if state is CampaignState.PAUSED:
        return "resume", "early_cutoff", "cancel"
    if state is CampaignState.SCHEDULED:
        return "early_cutoff", "cancel"
    if state is CampaignState.READY_TO_DRAW:
        return ("draw",)
    if state in {CampaignState.DRAFT, CampaignState.PUBLISHING}:
        return ("cancel",)
    return ()
