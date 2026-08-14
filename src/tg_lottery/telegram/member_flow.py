from datetime import datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from tg_lottery.application.invitation_models import accepts_attribution
from tg_lottery.application.participants import (
    ParticipantService,
    QualificationResult,
    RegistrationAttempt,
    RegistrationRequest,
)
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.required_chat_links import decrypt_required_chat_url
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.public_projection import campaign_state_text, format_public_time
from tg_lottery.telegram.qualification import observe_memberships


class MemberFlow:
    def __init__(
        self,
        *,
        bot: Bot,
        participants: ParticipantService,
        queries: QueryService,
        cipher: SecretCipher,
    ) -> None:
        self._bot = bot
        self._participants = participants
        self._queries = queries
        self._cipher = cipher

    async def register(
        self,
        message: Message,
        campaign_reference: str | UUID,
        *,
        user: User | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        campaign = await self._queries.campaign_by_reference(campaign_reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        user = user or _required_user(message)
        attempt = await self._participants.begin_registration(
            _registration_request(campaign.id, user, observed_at or message.date)
        )
        state = attempt.state
        observations: tuple[dict[str, object], ...] = ()
        if attempt.requires_observation:
            group = await self._queries.campaign_group(campaign.id)
            state, observations = await self._complete_observation(
                attempt, user_id=user.id, main_chat_id=group.chat_id
            )
        if state is QualificationState.ELIGIBLE:
            rules = CampaignRules.model_validate(campaign.rules)
            sent = await message.answer(
                _registration_success_text(rules),
                reply_markup=member_keyboard(campaign.public_ref, rules.invite_bonus_enabled),
            )
            await self._schedule_success_deletions(campaign, message, sent)
            return
        verify = self._qualification_keyboard(campaign, state, observations)
        if state is QualificationState.INELIGIBLE:
            text = "尚未满足全部群/频道参与条件。"
            if _extended_tips(campaign):
                text += " 完成后请重新验证。"
            await message.answer(text, reply_markup=verify)
        else:
            text = "当前无法核验群/频道资格，状态保持待核验。"
            if _extended_tips(campaign):
                text += " 请稍后点击验证资格重试。"
            await message.answer(text, reply_markup=verify)

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
        self, attempt: RegistrationAttempt, *, user_id: int, main_chat_id: int
    ) -> tuple[QualificationState, tuple[dict[str, object], ...]]:
        chat_ids = tuple(dict.fromkeys((main_chat_id, *attempt.required_chat_ids)))
        observed_at = utcnow()
        result = await observe_memberships(
            self._bot,
            chat_ids=chat_ids,
            user_id=user_id,
            observed_at=observed_at,
        )
        participant = await self._participants.complete_registration(
            QualificationResult(
                participant_id=attempt.participant_id,
                qualification_check_id=attempt.qualification_check_id,
                state=result.state,
                observed_at=observed_at,
                observed_status=result.observed_status,
                error_code=result.error_code,
                observations=result.observations,
            )
        )
        return participant.qualification_state, result.observations

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
            _draw_rule(rules),
            _human_rule(rules),
            f"发奖人员: {_issuer_name(campaign)}",
            _deletion_rule(campaign),
            _condition_summary(campaign),
        ]
        await message.answer("\n".join(lines), reply_markup=self._condition_keyboard(campaign))

    async def show_condition(
        self, message: Message, campaign_reference: str | UUID, position: int
    ) -> None:
        campaign = await self._queries.campaign_by_reference(campaign_reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        conditions = _conditions(campaign)
        if position < 1 or position > len(conditions):
            raise UserFacingError("参与条件不存在或已经失效")
        chat = conditions[position - 1]
        rules = CampaignRules.model_validate(campaign.rules)
        rows = []
        if not chat.get("invite_revoked_at") or chat.get("join_url"):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=_condition_action(chat), url=self._condition_url(campaign, chat)
                    )
                ]
            )
        navigation = _condition_navigation(campaign.public_ref, position, len(conditions))
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
        instruction = _condition_instruction(chat, rules)
        if chat.get("invite_revoked_at") and not chat.get("join_url"):
            instruction = f"「{chat['title']}」的加入入口已关闭。"
        await message.answer(
            f"参与条件 {position}/{len(conditions)}：{instruction}", reply_markup=keyboard
        )

    def _qualification_keyboard(
        self,
        campaign: object,
        state: QualificationState,
        observations: tuple[dict[str, object], ...],
    ) -> InlineKeyboardMarkup:
        failed = _failed_condition_positions(campaign, state, observations)
        rows = []
        if failed:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"查看未满足条件（{len(failed)}项）",
                        callback_data=f"member:condition:{campaign.public_ref}:{failed[0]}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="验证资格",
                    callback_data=f"member:verify:{campaign.public_ref}",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _condition_keyboard(self, campaign: object) -> InlineKeyboardMarkup | None:
        conditions = _conditions(campaign)
        if not conditions:
            return None
        rows = [
            [
                InlineKeyboardButton(
                    text=f"查看参与条件（{len(conditions)}项）",
                    callback_data=f"member:condition:{campaign.public_ref}:1",
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

    async def show_tickets(self, message: Message, user_id: int) -> None:
        summary = await self._queries.ticket_summary(user_id)
        if summary is None:
            await message.answer("当前没有参与中的活动。")
            return
        await message.answer(
            f"{summary.campaign_name}\n总抽奖次数: {summary.total}\n"
            f"基础票: {summary.registration}\n"
            f"发言加成: {summary.popularity}\n邀请加成: {summary.invitation}"
        )

    async def show_invitations(self, message: Message, user_id: int) -> None:
        summary = await self._queries.invitation_summary(user_id)
        if summary is None:
            await message.answer("当前没有邀请记录。")
            return
        await message.answer(
            f"待验证: {summary.pending}\n已生效: {summary.valid}\n已失效: {summary.invalid}"
        )

    async def show_invite_link(
        self, message: Message, campaign_reference: str | UUID, *, user_id: int
    ) -> None:
        campaign = await self._queries.campaign_by_reference(campaign_reference)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        rules = CampaignRules.model_validate(campaign.rules)
        if not rules.invite_bonus_enabled:
            raise UserFacingError("本期抽奖没有开启邀请加成")
        stored = await self._queries.invite_link_for_participant(campaign.id, user_id)
        if stored is None or stored.encrypted_url is None:
            raise UserFacingError("本期专属链接正在创建，或你尚未完成报名，请稍后再点")
        link = self._cipher.decrypt(stored.encrypted_url, context=f"invite-link:{stored.id}")
        active = accepts_attribution(
            campaign.state,
            preheat_at=campaign.preheat_at,
            cutoff_at=campaign.cutoff_at,
            event_at=utcnow(),
        )
        text = _invite_link_text(campaign.name, link, active=active)
        await message.answer(text, reply_markup=_invite_link_keyboard(link, campaign.name))


def member_keyboard(
    campaign_reference: str | None = None, invite_bonus_enabled: bool = False
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="我的抽奖次数", callback_data="member:tickets")]]
    if invite_bonus_enabled and campaign_reference:
        rows.append(
            [
                InlineKeyboardButton(
                    text="获取本期邀请链接",
                    callback_data=f"member:invite:{campaign_reference}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="邀请记录", callback_data="member:invites")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _invite_link_text(campaign_name: str, link: str, *, active: bool) -> str:
    if active:
        note = "仅在本期报名截止前通过该链接加入，才会计算本期邀请加成。"
    else:
        note = "本期已截止；此链接仍可入群，但不再计算本期或以后任何抽奖加成。"
    return f"🔗 {campaign_name} 专属邀请链接\n\n{link}\n\n{note}"


def _invite_link_keyboard(link: str, campaign_name: str) -> InlineKeyboardMarkup:
    share = "https://t.me/share/url?url=" + quote(link, safe="")
    share += "&text=" + quote(f"邀请你参加「{campaign_name}」抽奖", safe="")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="打开群聊", url=link)],
            [InlineKeyboardButton(text="转发给好友", url=share)],
        ]
    )


def _required_user(message: Message) -> User:
    if message.from_user is None:
        raise ValueError("Telegram message has no sender")
    return message.from_user


def _registration_request(
    campaign_id: UUID, user: User, observed_at: datetime
) -> RegistrationRequest:
    return RegistrationRequest(
        campaign_id=campaign_id,
        user_id=user.id,
        username=user.username,
        display_name=user.full_name,
        observed_at=observed_at,
    )


def _extended_tips(campaign: object) -> bool:
    presentation = getattr(campaign, "presentation", {}) or {}
    return bool(presentation.get("message_tips", True))


def _conditions(campaign: object) -> tuple[dict[str, object], ...]:
    presentation = getattr(campaign, "presentation", {}) or {}
    main = presentation.get("main_chat")
    extras = presentation.get("required_chats") or []
    values = ([main] if isinstance(main, dict) else []) + list(extras)
    return tuple(item for item in values if isinstance(item, dict))


def _draw_rule(rules: CampaignRules) -> str:
    if rules.draw_mode == "full":
        return f"开奖方式: 满 {rules.participant_target} 人后进入结算，30 天中午兜底"
    return "开奖方式: 定时开奖"


def _human_rule(rules: CampaignRules) -> str:
    if not rules.bonus_types:
        return "抽奖加成: 关闭，仅计算基础抽奖次数"
    parts = []
    if rules.invite_bonus_enabled:
        parts.append(_invite_rule_text(rules))
    if rules.activity_bonus_enabled:
        parts.append(
            f"每个条件群每天有效发言 {rules.activity.message_threshold} 条，"
            f"全部达标+{rules.activity.reward_tickets} 次"
        )
    return "抽奖加成: " + "；".join(parts)


def _invite_rule_text(rules: CampaignRules) -> str:
    if not rules.invite.reward_tiers:
        return f"每个有效邀请+{rules.invite.reward_tickets}次"
    tiers = ", ".join(
        f"{tier.invite_count}人+{tier.total_bonus}次" for tier in rules.invite.reward_tiers
    )
    return f"邀请 {tiers}，上限 {rules.invite.max_bonus_tickets} 次"


def _registration_success_text(rules: CampaignRules) -> str:
    if rules.invite_bonus_enabled:
        return "报名成功。专属邀请链接创建并确认后会发送到这里。"
    return "报名成功。"


def _issuer_name(campaign: object) -> str:
    presentation = getattr(campaign, "presentation", {}) or {}
    return str(presentation.get("issuer_name") or "活动创建者")


def _deletion_rule(campaign: object) -> str:
    presentation = getattr(campaign, "presentation", {}) or {}
    trigger = "删除报名命令" if presentation.get("delete_trigger_message") else "保留报名命令"
    seconds = presentation.get("completion_delete_after_seconds")
    if seconds is None:
        return f"消息删除: {trigger}; 完成提示不自动删除"
    return f"消息删除: {trigger}; 完成提示 {int(seconds)} 秒后删除"


def _condition_summary(campaign: object) -> str:
    labels = []
    for chat in _conditions(campaign):
        suffix = " (加入入口已关闭)" if chat.get("invite_revoked_at") else ""
        labels.append(f"{chat['title']}{suffix}")
    if len(labels) > 3:
        return f"参与条件: 共 {len(labels)} 项（请点击逐项查看）"
    return "参与条件: " + ("、".join(labels) if labels else "无额外条件")


def _condition_action(chat: dict[str, object]) -> str:
    return "关注频道" if chat.get("type") == "channel" else "加入群聊"


def _condition_instruction(chat: dict[str, object], rules: CampaignRules) -> str:
    instruction = f"请{_condition_action(chat)}「{chat['title']}」"
    if rules.activity_bonus_enabled and chat.get("type") != "channel":
        instruction += f"；本群每天需发送 {rules.activity.message_threshold} 条有效消息"
    return instruction + "，完成加入后点击验证资格。"


def _condition_navigation(
    campaign_ref: str, position: int, count: int
) -> list[InlineKeyboardButton]:
    buttons = []
    if position > 1:
        buttons.append(
            InlineKeyboardButton(
                text="← 上一项", callback_data=f"member:condition:{campaign_ref}:{position - 1}"
            )
        )
    if position < count:
        buttons.append(
            InlineKeyboardButton(
                text="下一项 →", callback_data=f"member:condition:{campaign_ref}:{position + 1}"
            )
        )
    return buttons


def _failed_condition_positions(
    campaign: object,
    state: QualificationState,
    observations: tuple[dict[str, object], ...],
) -> tuple[int, ...]:
    failed_ids = {int(item["chat_id"]) for item in observations if item["state"] == state.value}
    return tuple(
        position
        for position, chat in enumerate(_conditions(campaign), start=1)
        if int(chat["id"]) in failed_ids
    )
