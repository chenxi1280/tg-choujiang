from uuid import UUID

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.callback_tokens import (
    CallbackGrant,
    CallbackRequest,
    CallbackTokenService,
)
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.winners import WinnerService
from tg_lottery.domain.enums import CampaignState, PrizeClaimState
from tg_lottery.telegram.admin_authorization import verify_chat_admin
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_actions import CREATOR_ACTIONS


class AdminFlow:
    def __init__(
        self,
        *,
        bot: Bot,
        creator: CampaignCreator,
        tokens: CallbackTokenService,
        campaigns: CampaignService,
        drawing: DrawingService,
        winners: WinnerService,
        queries: QueryService,
    ) -> None:
        self._bot = bot
        self._creator = creator
        self._tokens = tokens
        self._campaigns = campaigns
        self._drawing = drawing
        self._winners = winners
        self._queries = queries

    async def show_main(self, message: Message) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="创建/继续抽奖", callback_data="nav:create")],
                [InlineKeyboardButton(text="我的活动", callback_data="nav:campaigns")],
                [InlineKeyboardButton(text="我的票数", callback_data="member:tickets")],
                [InlineKeyboardButton(text="邀请记录", callback_data="member:invites")],
            ]
        )
        await message.answer("请选择操作：", reply_markup=keyboard)

    async def start_create(self, query: CallbackQuery) -> None:
        await self._creator.start(query)

    async def start_create_message(self, message: Message) -> None:
        await self._creator.start_message(message)

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
        return await self._creator.handle_message(message)

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        if grant.action in CREATOR_ACTIONS:
            await self._creator.handle_grant(query, grant)
            return
        handlers = {
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
        await verify_chat_admin(
            self._bot,
            chat_id,
            user_id,
            require_invites=require_invites,
        )

    async def _required_campaign(self, campaign_id: UUID):
        campaign = await self._queries.campaign(campaign_id)
        if campaign is None:
            raise ValueError("活动不存在")
        return campaign


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message


def _campaign_id(grant: CallbackGrant) -> UUID:
    if grant.campaign_id is None:
        raise ValueError("callback is missing campaign binding")
    return grant.campaign_id


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
