from datetime import datetime, timedelta
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from tg_lottery.application.campaign_targets import QualificationTarget, uses_multi_group_contract
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.application.participants import (
    ParticipantService,
    QualificationResult,
    RegistrationAttempt,
)
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.required_chat_links import decrypt_required_chat_url
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram import member_callbacks, member_condition_views
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.member_group_invites import build_group_invite_payload
from tg_lottery.telegram.member_invites import legacy_invite_link_payload, member_keyboard
from tg_lottery.telegram.member_missing_targets import build_missing_targets_reply
from tg_lottery.telegram.member_observation import apply_message_thresholds
from tg_lottery.telegram.member_popups import popup_success
from tg_lottery.telegram.member_records import (
    show_invitation_groups,
    show_ticket_record,
)
from tg_lottery.telegram.member_referral_landing import show_referral_landing
from tg_lottery.telegram.member_registration import (
    registration_request,
    registration_request_from_message,
    required_user,
)
from tg_lottery.telegram.public_projection import campaign_state_text, format_public_time
from tg_lottery.telegram.qualification import (
    QualificationRetryable,
    observe_memberships,
    with_pending_approvals,
)


class MemberFlow:
    def __init__(
        self,
        *,
        bot: Bot,
        participants: ParticipantService,
        queries: QueryService,
        cipher: SecretCipher,
        stats: MessageStatsService | None = None,
        invitations: InvitationService | None = None,
        bot_username: str = "",
    ) -> None:
        self._bot = bot
        self._participants = participants
        self._queries = queries
        self._cipher = cipher
        self._stats = stats
        self._invitations = invitations
        self._bot_username = bot_username

    async def show_referral_landing(self, message: Message, public_ref: str) -> None:
        await show_referral_landing(self, message, public_ref)

    async def membership_changed(
        self,
        *,
        campaign_id: UUID,
        user: User,
        chat_id: int,
        is_member: bool,
        observed_at: datetime,
    ) -> None:
        if not is_member:
            await self._participants.mark_membership_ineligible(
                campaign_id=campaign_id,
                user_id=user.id,
                chat_id=chat_id,
                observed_at=observed_at,
            )
            return
        campaign = await self._queries.campaign(campaign_id)
        if campaign is None:
            return
        attempt = await self._participants.begin_registration(
            registration_request(campaign.id, user, observed_at)
        )
        if not attempt.requires_observation:
            return
        group = await self._queries.campaign_group(campaign.id)
        await self._complete_observation(
            attempt, user_id=user.id, main_chat_id=group.chat_id, campaign=campaign
        )

    async def join_request_pending(
        self, *, campaign_id: UUID, user: User, chat_id: int, observed_at: datetime
    ) -> None:
        await self._participants.mark_membership_ineligible(
            campaign_id=campaign_id,
            user_id=user.id,
            chat_id=chat_id,
            observed_at=observed_at,
            observed_status="join_request_pending",
        )

    async def register(
        self,
        message: Message,
        campaign_reference: str | UUID,
        *,
        user: User | None = None,
        observed_at: datetime | None = None,
        callback: CallbackQuery | None = None,
        missing_page: int = 1,
    ) -> None:
        campaign = await self._queries.campaign_by_reference(campaign_reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        user = user or required_user(message)
        existing = await self._participants.eligible_participant(campaign.id, user.id)
        if existing is not None:
            await member_callbacks.already_reply(callback, message, existing.ticket_total_cache)
            return
        attempt = await self._participants.begin_registration(
            registration_request_from_message(
                campaign.id,
                user,
                message,
                observed_at=observed_at,
            )
        )
        try:
            state, observations, total = await self._registration_result(
                attempt, user_id=user.id, campaign=campaign
            )
        except QualificationRetryable:
            await self._qualification_retry_reply(message, callback)
            return
        if state is QualificationState.ELIGIBLE:
            if total == 0:
                replay = await self._participants.eligible_participant(campaign.id, user.id)
                total = replay.ticket_total_cache if replay is not None else 1
            await self._registration_success_reply(message, campaign, total, callback=callback)
            return
        await self._send_qualification_failure(
            message,
            campaign=campaign,
            state=state,
            observations=observations,
            user_id=user.id,
            page=missing_page,
            callback=callback,
        )

    async def _registration_result(
        self, attempt: RegistrationAttempt, *, user_id: int, campaign: object
    ) -> tuple[QualificationState, tuple[dict[str, object], ...], int]:
        if not attempt.requires_observation:
            return attempt.state, (), 0
        group = await self._queries.campaign_group(campaign.id)
        return await self._complete_observation(
            attempt, user_id=user_id, main_chat_id=group.chat_id, campaign=campaign
        )

    async def _qualification_retry_reply(
        self, message: Message, callback: CallbackQuery | None
    ) -> None:
        text = "当前无法核验群/频道资格，请稍后再试"
        if callback is not None:
            await member_callbacks.safe_answer(callback, text, alert=True)
            return
        await message.answer(text)

    async def _registration_success_reply(
        self, message: Message, campaign: object, total: int, *, callback: CallbackQuery | None
    ) -> None:
        chat_type = getattr(getattr(message, "chat", None), "type", None)
        if callback is not None and chat_type != "private":
            await member_callbacks.safe_answer(callback, popup_success(total), alert=False)
            return
        rules = CampaignRules.model_validate(campaign.rules)
        keyboard = member_keyboard(
            campaign.public_ref,
            bool(rules.bonus_types),
            invite_enabled=rules.invite_bonus_enabled,
        )
        if callback is not None:
            await message.edit_text(popup_success(total), reply_markup=keyboard)
            await member_callbacks.safe_answer(callback, popup_success(total), alert=False)
            return
        sent = await message.answer(popup_success(total), reply_markup=keyboard)
        await self._schedule_success_deletions(campaign, message, sent)
        if callback is not None:
            await member_callbacks.safe_answer(callback, popup_success(total), alert=False)

    async def register_callback(self, query: CallbackQuery, campaign_reference: str | UUID) -> None:
        await member_callbacks.register_callback(self, query, campaign_reference)

    async def bonus_callback(self, query: CallbackQuery, campaign_reference: str | UUID) -> None:
        await member_callbacks.bonus_callback(self, query, campaign_reference)

    async def rules_callback(self, query: CallbackQuery, campaign_reference: str | UUID) -> None:
        await member_callbacks.rules_callback(self, query, campaign_reference)

    async def _schedule_success_deletions(
        self, campaign: object, trigger: Message, completion: Message
    ) -> None:
        presentation = getattr(campaign, "presentation", {}) or {}
        now = utcnow()
        if presentation.get("delete_trigger_message"):
            await self._participants.schedule_message_deletion(
                campaign_id=campaign.id,
                chat_id=trigger.chat.id,
                message_id=trigger.message_id,
                run_at=now,
            )
        seconds = presentation.get("completion_delete_after_seconds")
        if seconds is None:
            return
        await self._participants.schedule_message_deletion(
            campaign_id=campaign.id,
            chat_id=completion.chat.id,
            message_id=completion.message_id,
            run_at=now + timedelta(seconds=int(seconds)),
        )

    async def _complete_observation(
        self,
        attempt: RegistrationAttempt,
        *,
        user_id: int,
        main_chat_id: int,
        campaign: object,
    ) -> tuple[QualificationState, tuple[dict[str, object], ...], int]:
        chat_ids = tuple(dict.fromkeys((main_chat_id, *attempt.required_chat_ids)))
        observed_at = utcnow()
        result = await observe_memberships(
            self._bot,
            chat_ids=chat_ids,
            user_id=user_id,
            observed_at=observed_at,
        )
        pending_chat_ids = await self._participants.pending_approval_chat_ids(
            attempt.participant_id
        )
        rules = CampaignRules.model_validate(campaign.rules)
        thresholds = {int(key): int(value) for key, value in rules.message_thresholds.items()}
        observations = member_condition_views.with_target_kinds(
            with_pending_approvals(result.observations, pending_chat_ids),
            attempt.target_kinds,
        )
        state, observations = await apply_message_thresholds(
            self._stats,
            result.state,
            observations,
            attempt=attempt,
            user_id=user_id,
            observed_at=observed_at,
            thresholds=thresholds,
        )
        participant = await self._participants.complete_registration(
            QualificationResult(
                participant_id=attempt.participant_id,
                qualification_check_id=attempt.qualification_check_id,
                state=state,
                observed_at=observed_at,
                observed_status=result.observed_status,
                error_code=result.error_code,
                observations=observations,
            )
        )
        return participant.qualification_state, observations, participant.ticket_total_cache

    async def show_rules(self, message: Message, campaign_reference: str | UUID) -> None:
        campaign = await self._queries.campaign_by_reference(campaign_reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        prizes = await self._queries.prizes(campaign.id)
        rules = CampaignRules.model_validate(campaign.rules)
        lines = [
            f"📋 {campaign.name} 活动规则",
            f"当前状态: {campaign_state_text(campaign.state)}",
            "奖品: " + "、".join(f"{item.name} × {item.winner_count}" for item in prizes),
            f"开奖时间: {format_public_time(campaign.draw_at, campaign.timezone)}",
            member_condition_views.draw_rule(rules),
            member_condition_views.human_rule(rules, member_condition_views.conditions(campaign)),
            f"发奖人员: {member_condition_views.issuer_name(campaign)}",
            member_condition_views.deletion_rule(campaign),
            member_condition_views.condition_summary(campaign),
        ]
        await message.answer("\n".join(lines), reply_markup=self._condition_keyboard(campaign))

    async def show_condition(
        self, message: Message, campaign_reference: str | UUID, position: int
    ) -> None:
        campaign = await self._queries.campaign_by_reference(campaign_reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        conditions = member_condition_views.conditions(campaign)
        if position < 1 or position > len(conditions):
            raise UserFacingError("参与条件不存在或已经失效")
        chat = conditions[position - 1]
        rules = CampaignRules.model_validate(campaign.rules)
        rows = []
        if not chat.get("invite_revoked_at") or chat.get("join_url"):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=member_condition_views.condition_action(chat),
                        url=self._condition_url(campaign, chat),
                    )
                ]
            )
        navigation = member_condition_views.condition_navigation(
            campaign.public_ref, position, len(conditions)
        )
        if navigation:
            rows.append(navigation)
        rows.append(
            [
                InlineKeyboardButton(
                    text="验证资格", callback_data=f"member:verify:{campaign.public_ref}"
                )
            ]
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        instruction = member_condition_views.condition_instruction(chat, rules)
        if chat.get("invite_revoked_at") and not chat.get("join_url"):
            instruction = f"「{chat['title']}」的加入入口已关闭。"
        await message.answer(
            f"参与条件 {position}/{len(conditions)}：{instruction}", reply_markup=keyboard
        )

    async def _send_qualification_failure(
        self,
        message: Message,
        *,
        campaign: object,
        state: QualificationState,
        observations: tuple[dict[str, object], ...],
        user_id: int,
        page: int,
        callback: CallbackQuery | None,
    ) -> None:
        text, keyboard = await self._qualification_reply(campaign, state, observations, page=page)
        chat_type = getattr(getattr(message, "chat", None), "type", None)
        if callback is not None:
            if chat_type == "private":
                await member_callbacks.alert_failure(
                    callback, campaign, state, observations=observations
                )
            else:
                await member_callbacks.safe_answer(
                    callback,
                    "尚未满足参与条件，请私聊机器人查看详情",
                    alert=True,
                )
        if callback is not None and chat_type == "private":
            await message.edit_text(text, reply_markup=keyboard)
            return
        if chat_type in {None, "private"}:
            await message.answer(text, reply_markup=keyboard)
            return
        try:
            await self._bot.send_message(user_id, text, reply_markup=keyboard)
        except TelegramAPIError:
            return

    async def _qualification_reply(
        self,
        campaign: object,
        state: QualificationState,
        observations: tuple[dict[str, object], ...],
        *,
        page: int = 1,
    ) -> tuple[str, InlineKeyboardMarkup]:
        targets = await self._qualification_targets(campaign)
        return build_missing_targets_reply(
            campaign,
            state,
            targets,
            observations=observations,
            page=page,
            target_url=self._target_url,
        )

    async def _qualification_targets(self, campaign: object) -> tuple[QualificationTarget, ...]:
        loader = getattr(self._queries, "qualification_targets", None)
        if loader is not None:
            return await loader(campaign.id)
        if uses_multi_group_contract(campaign):
            raise RuntimeError("multi-group query service cannot load qualification targets")
        return ()

    def _target_url(self, campaign: object, target: QualificationTarget) -> str:
        if target.public_join_url:
            return str(target.public_join_url)
        if target.encrypted_join_url is None or target.revoked_at is not None:
            raise UserFacingError("该加入入口尚未准备好或已经撤销")
        return self._cipher.decrypt(
            target.encrypted_join_url,
            context=target.secret_context,
        )

    def _condition_keyboard(self, campaign: object) -> InlineKeyboardMarkup | None:
        conditions = member_condition_views.conditions(campaign)
        if not conditions:
            return None
        rows = [
            [
                InlineKeyboardButton(
                    text=f"查看加入入口（{len(conditions)}项）",
                    url=f"https://t.me/{self._bot_username}?start=join_{campaign.public_ref}",
                )
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _condition_url(self, campaign: object, chat: dict[str, object]) -> str:
        if chat.get("join_url"):
            return str(chat["join_url"])
        if chat.get("invite_revoked_at"):
            raise UserFacingError("该参与条件邀请链接已撤销")
        return decrypt_required_chat_url(self._cipher, campaign.id, chat)

    async def show_tickets(self, message: Message, user_id: int, *, public_ref: str) -> None:
        await show_ticket_record(message, self._queries, user_id, public_ref=public_ref)

    async def show_invitations(
        self, message: Message, user_id: int, *, public_ref: str, page: int = 0
    ) -> None:
        await show_invitation_groups(
            message, self._queries, user_id, public_ref=public_ref, page=page
        )

    async def show_invite_link(
        self,
        message: Message,
        campaign_reference: str | UUID,
        *,
        user_id: int,
        page: int = 0,
    ) -> None:
        campaign = await self._queries.campaign_by_reference(campaign_reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        rules = CampaignRules.model_validate(campaign.rules)
        if not rules.invite_bonus_enabled:
            raise UserFacingError("本期抽奖没有开启邀请加成")
        text, markup = await self._invite_link_payload(campaign, user_id, page=page)
        await message.answer(text, reply_markup=markup)

    async def _invite_link_payload(
        self, campaign: object, user_id: int, *, page: int = 0
    ) -> tuple[str, InlineKeyboardMarkup]:
        rules = CampaignRules.model_validate(campaign.rules)
        if rules.group_invite_rules:
            participant = await self._participants.eligible_participant(campaign.id, user_id)
            if participant is None:
                raise UserFacingError("请先点击「立即参与」并完成全部参与群资格校验")
            return await build_group_invite_payload(
                campaign,
                user_id=user_id,
                page=page,
                queries=self._queries,
                cipher=self._cipher,
            )
        return await legacy_invite_link_payload(
            campaign,
            user_id=user_id,
            queries=self._queries,
            cipher=self._cipher,
        )

    async def _send_invite_link_to(self, user_id: int, campaign: object) -> None:
        text, markup = await self._invite_link_payload(campaign, user_id)
        await self._bot.send_message(user_id, text, reply_markup=markup)
