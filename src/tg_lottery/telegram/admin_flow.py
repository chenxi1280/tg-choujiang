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
from tg_lottery.application.publication_queries import PublicationIssue
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.winners import WinnerService
from tg_lottery.domain.action_confirmation import ExpectedCampaign
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.platform_policy import PlatformPolicy
from tg_lottery.telegram.admin_authorization import verify_chat_admin
from tg_lottery.telegram.admin_confirmations import (
    ACTION_LABELS,
    CONFIRMED_ACTIONS,
    confirmation_text,
    validate_available_action,
    validate_confirmation,
)
from tg_lottery.telegram.admin_content_pages import render_management_panel
from tg_lottery.telegram.admin_management_views import MANAGEMENT_ACTION_LABELS, management_text
from tg_lottery.telegram.admin_winner_pages import winner_page_view
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.campaign_authorization import verify_campaign_admin
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_list_views import campaign_origin, creator_list_view
from tg_lottery.telegram.platform_requirements import inspect_official_channels
from tg_lottery.telegram.public_projection import campaign_state_text
from tg_lottery.telegram.publication_error_text import publication_error_text

DRAW_CONFIRMATION_TEXT = "开奖结果已生成, 群公告和中奖通知正在发送。"


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
                [InlineKeyboardButton(text="我的抽奖次数", callback_data="member:tickets")],
                [InlineKeyboardButton(text="邀请记录", callback_data="member:invites")],
            ]
        )
        await message.answer("请选择操作：", reply_markup=keyboard)

    async def start_create(self, query: CallbackQuery) -> None:
        await self._creator.start(query)

    async def suspend_draft(self, user_id: int) -> None:
        await self._creator.suspend(user_id)

    async def start_new(self, query: CallbackQuery) -> None:
        await self._creator.start_new(query)

    async def start_create_message(self, message: Message) -> None:
        await self._creator.start_message(message)

    async def recover_applied_draft_callback(self, query: CallbackQuery) -> None:
        await self._creator.recover_applied_callback(query)

    async def recover_draft_callback(self, query: CallbackQuery) -> bool:
        return await self._creator.recover_expired_panel(query)

    async def show_campaigns(self, query: CallbackQuery) -> None:
        result = await self._queries.creator_campaign_page(
            query.from_user.id, options={"managed": True, "category": "active", "page": 0}
        )
        text, keyboard = creator_list_view(result)
        await _message(query).edit_text(text, reply_markup=keyboard)

    async def show_campaign_reference(self, query: CallbackQuery, reference: str) -> None:
        reference, origin = campaign_origin(reference)
        campaign = await self._queries.campaign_by_reference(reference)
        if campaign is None:
            raise ValueError("活动不存在")
        await self._verify_admin(campaign.id, query.from_user.id)
        await self._show_campaign(
            _message(query), campaign.id, user_id=query.from_user.id, origin=origin
        )

    async def handle_text(self, message: Message) -> bool:
        return await self._creator.handle_message(message)

    async def handle_grant(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        if self._creator.handles_action(grant.action):
            await self._creator.handle_grant(query, grant)
            return
        if grant.action in CONFIRMED_ACTIONS:
            await self._confirm_action(query, grant)
            return
        if grant.action.startswith("confirm:"):
            await self._execute_confirmation(query, grant)
            return
        handlers = {
            "view_campaign": self._view_campaign,
            "view_winners": self._view_winners,
            "pause": self._pause,
            "resume": self._resume,
            "early_cutoff": self._early_cutoff,
            "draw": self._draw,
            "cancel": self._cancel,
            "mark_claimed": self._mark_claimed,
            "refresh_publication": self._refresh_publication,
            "publication_issues": self._publication_issues,
            "retry_publication": self._retry_publication,
            "retry_qualification": self._retry_qualification,
        }
        handler = handlers.get(grant.action)
        if handler is None:
            raise ValueError(f"unknown callback action: {grant.action}")
        await handler(query, grant)

    async def _confirm_action(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        campaign = await self._required_campaign(campaign_id)
        validate_available_action(campaign, grant.action)
        payload = dict(grant.payload or {}) | {
            "expected_state": str(campaign.state),
            "expected_revision": campaign.config_revision,
        }
        requests = (
            CallbackRequest("confirm:" + grant.action, campaign_id=campaign_id, payload=payload),
            CallbackRequest("view_campaign", campaign_id=campaign_id, payload=grant.payload),
        )
        tokens = await self._tokens.create_many(
            owner_user_id=query.from_user.id, chat_id=_message(query).chat.id, requests=requests
        )
        rows = [
            [
                InlineKeyboardButton(
                    text="确认" + ACTION_LABELS[grant.action], callback_data=f"secure:{tokens[0]}"
                )
            ],
            [InlineKeyboardButton(text="返回活动管理", callback_data=f"secure:{tokens[1]}")],
        ]
        await _message(query).edit_text(
            confirmation_text(campaign, grant.action, details=grant.payload),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _execute_confirmation(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        from dataclasses import replace

        action = grant.action.removeprefix("confirm:")
        if action not in CONFIRMED_ACTIONS:
            raise UserFacingError("该确认操作不存在")
        campaign = await self._required_campaign(_campaign_id(grant))
        validate_available_action(campaign, action)
        validate_confirmation(campaign, grant.payload)
        handlers = {
            "cancel": self._cancel,
            "early_cutoff": self._early_cutoff,
            "draw": self._draw,
            "mark_claimed": self._mark_claimed,
            "retry_publication": self._retry_publication,
        }
        await handlers[action](query, replace(grant, action=action))
        await self._show_campaign(
            _message(query),
            campaign.id,
            user_id=query.from_user.id,
            origin=(grant.payload or {}).get("origin", "nav:created"),
        )

    async def _view_campaign(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._show_campaign(
            _message(query),
            campaign_id,
            user_id=query.from_user.id,
            origin=(grant.payload or {}).get("origin", "nav:created"),
            content_page=int((grant.payload or {}).get("content_page", 0)),
        )

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
            expected=_expected(grant),
        )
        await _message(query).answer(f"活动状态已更新为{campaign_state_text(target)}。")

    async def _early_cutoff(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._campaigns.early_cutoff(
            campaign_id,
            actor_user_id=query.from_user.id,
            cutoff_at=utcnow(),
            reason="管理员提前截止",
            expected=_expected(grant),
        )
        await _message(query).answer("活动已进入结算中；排空与资格复核完成后才可开奖。")

    async def _draw(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._drawing.run(campaign_id, query.from_user.id, expected=_expected(grant))
        await _message(query).answer(DRAW_CONFIRMATION_TEXT)

    async def _mark_claimed(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        await self._verify_admin(_campaign_id(grant), query.from_user.id)
        winner_id = UUID(str(grant.payload["winner_id"]))
        await self._winners.mark_claimed(winner_id, query.from_user.id, expected=_expected(grant))
        await _message(query).answer("领奖状态已登记。")

    async def _refresh_publication(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._show_campaign(
            _message(query),
            campaign_id,
            user_id=query.from_user.id,
            origin=(grant.payload or {}).get("origin", "nav:created"),
        )

    async def _retry_publication(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        campaign = await self._queries.campaign(campaign_id)
        if campaign is None:
            raise UserFacingError("活动状态暂未核实，请刷新后重试")
        frozen = campaign.rules.get("platform_policy")
        await inspect_official_channels(
            self._bot, PlatformPolicy.model_validate(frozen) if frozen else None
        )
        count = await self._campaigns.retry_failed_publication(
            campaign_id, query.from_user.id, expected=_expected(grant)
        )
        await _message(query).answer(f"已将 {count} 个明确失败群重新排队。")
        await self._show_campaign(
            _message(query),
            campaign_id,
            user_id=query.from_user.id,
            origin=(grant.payload or {}).get("origin", "nav:created"),
        )

    async def _publication_issues(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        issues = await self._queries.publication_issues(campaign_id)
        if not issues:
            await _message(query).answer("当前没有明确失败或远端结果未知的群或频道。")
            return
        await _message(query).answer(_issues_text("发布异常的群或频道：", issues))

    async def _retry_qualification(self, query: CallbackQuery, grant: CallbackGrant) -> None:
        campaign_id = _campaign_id(grant)
        await self._verify_admin(campaign_id, query.from_user.id)
        await self._campaigns.retry_qualification_review(campaign_id, query.from_user.id)
        await _message(query).answer("资格复核已重新排队；阻断会在全部目标恢复后自动解除。")

    async def _show_campaign(
        self,
        message: Message,
        campaign_id: UUID,
        *,
        user_id: int,
        origin: str = "nav:created",
        content_page: int = 0,
    ) -> None:
        campaign = await self._required_campaign(campaign_id)
        stats = await self._queries.campaign_stats(campaign_id)
        deliveries = await self._queries.delivery_summary(campaign_id)
        details = await self._queries.management_details(campaign_id)
        issues = await self._load_publication_issues(campaign.id, campaign.state)
        actions = (
            _campaign_actions(campaign.state)
            + _publication_actions(campaign.state, issues)
            + (("view_campaign",) if campaign.state is not CampaignState.PUBLISHING else ())
        )
        if await self._queries.qualification_review_blocked(campaign.id):
            actions += ("retry_qualification",)
        requests = tuple(
            CallbackRequest(action, campaign_id=campaign_id, payload={"origin": origin})
            for action in actions
        )
        tokens = await self._tokens.create_many(
            owner_user_id=user_id,
            chat_id=message.chat.id,
            requests=requests,
        )
        buttons = [
            [
                InlineKeyboardButton(
                    text=MANAGEMENT_ACTION_LABELS[action], callback_data=f"secure:{token}"
                )
            ]
            for action, token in zip(actions, tokens, strict=True)
        ]
        text = management_text(campaign, stats=stats, deliveries=deliveries, details=details)
        buttons.extend(_management_footer(origin))
        await render_management_panel(
            self._tokens,
            message=message,
            user_id=user_id,
            campaign_id=campaign_id,
            text=text,
            buttons=buttons,
            origin=origin,
            content_page=content_page,
        )

    async def _load_publication_issues(
        self, campaign_id: UUID, state: CampaignState
    ) -> tuple[PublicationIssue, ...]:
        if state is not CampaignState.PUBLISHING:
            return ()
        return await self._queries.publication_issues(campaign_id)

    async def _view_winners(self, query, grant):
        await self._verify_admin(_campaign_id(grant), query.from_user.id)
        await winner_page_view(self._queries, self._tokens, query=query, grant=grant)

    async def _verify_admin(
        self, campaign_id: UUID, user_id: int, *, require_invites: bool = False
    ) -> None:
        await verify_campaign_admin(
            self._bot,
            self._queries,
            campaign_id=campaign_id,
            user_id=user_id,
            require_invites=require_invites,
        )

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
    if state is CampaignState.DRAWN:
        return ("view_winners",)
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


def _publication_actions(
    state: CampaignState, issues: tuple[PublicationIssue, ...]
) -> tuple[str, ...]:
    if state is not CampaignState.PUBLISHING:
        return ()
    actions = ["refresh_publication"]
    if issues:
        actions.append("publication_issues")
    if any(issue.state is OutboxState.PRE_ACCEPT_FAILED for issue in issues):
        actions.append("retry_publication")
    return tuple(actions)


def _issues_text(title: str, issues: tuple[PublicationIssue, ...]) -> str:
    if not issues:
        return f"{title}\n当前没有异常记录。"
    lines = [title]
    for issue in issues:
        state = _publication_issue_state(issue.state)
        name = chat_title({"id": issue.chat_id, "title": issue.title})
        reason = publication_error_text(issue.error)
        stage = {
            "create_required_chat_invite": "生成加入入口",
            "send_campaign_rules": "发送完整规则页",
        }.get(issue.operation, "发送活动卡")
        lines.append(f"• {name} · {stage}\n  {state}：{reason}")
    return "\n".join(lines)


def _publication_issue_state(state: OutboxState) -> str:
    if state is OutboxState.PRE_ACCEPT_FAILED:
        return "明确失败，可安全重试"
    return "远端结果未知，不会自动重试"


def _expected(grant):
    payload = grant.payload or {}
    if "expected_state" not in payload:
        return None
    return ExpectedCampaign(payload["expected_state"], payload["expected_revision"])


def _management_footer(origin):
    return [
        [InlineKeyboardButton(text="返回活动列表", callback_data=origin)],
        [InlineKeyboardButton(text="返回首页", callback_data="nav:index")],
    ]
