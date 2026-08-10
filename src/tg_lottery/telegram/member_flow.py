from datetime import datetime
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.participants import (
    ParticipantService,
    QualificationResult,
    RegistrationAttempt,
    RegistrationRequest,
)
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules


class MemberFlow:
    def __init__(
        self,
        *,
        bot: Bot,
        participants: ParticipantService,
        queries: QueryService,
        campaigns: CampaignService,
    ) -> None:
        self._bot = bot
        self._participants = participants
        self._queries = queries
        self._campaigns = campaigns

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
        if attempt.requires_observation:
            group = await self._queries.campaign_group(campaign.id)
            state = await self._complete_observation(
                attempt, user_id=user.id, main_chat_id=group.chat_id
            )
        if state is QualificationState.ELIGIBLE:
            await self._trigger_full_draw(campaign)
            text = "报名成功。"
            if _extended_tips(campaign):
                text += " 专属邀请链接创建并确认后会发送到这里。"
            await message.answer(text, reply_markup=member_keyboard())
            return
        verify = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="验证资格",
                        callback_data=f"member:verify:{campaign.public_ref}",
                    )
                ]
            ]
        )
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

    async def _complete_observation(
        self, attempt: RegistrationAttempt, *, user_id: int, main_chat_id: int
    ) -> QualificationState:
        chat_ids = tuple(dict.fromkeys((main_chat_id, *attempt.required_chat_ids)))
        state, status, error = await self._qualification(chat_ids, user_id)
        participant = await self._participants.complete_registration(
            QualificationResult(
                participant_id=attempt.participant_id,
                qualification_check_id=attempt.qualification_check_id,
                state=state,
                observed_at=utcnow(),
                observed_status=status,
                error_code=error,
            )
        )
        return participant.qualification_state

    async def show_tickets(self, message: Message, user_id: int) -> None:
        summary = await self._queries.ticket_summary(user_id)
        if summary is None:
            await message.answer("当前没有参与中的活动。")
            return
        await message.answer(
            f"{summary.campaign_name}\n总票数: {summary.total}\n"
            f"基础票: {summary.registration}\n"
            f"人气票: {summary.popularity}\n邀请票: {summary.invitation}"
        )

    async def show_invitations(self, message: Message, user_id: int) -> None:
        summary = await self._queries.invitation_summary(user_id)
        if summary is None:
            await message.answer("当前没有邀请记录。")
            return
        await message.answer(
            f"待验证: {summary.pending}\n已生效: {summary.valid}\n已失效: {summary.invalid}"
        )

    async def _qualification(
        self, chat_ids: tuple[int, ...], user_id: int
    ) -> tuple[QualificationState, str, str | None]:
        if not chat_ids:
            return QualificationState.ELIGIBLE, "not_required", None
        statuses: list[str] = []
        for chat_id in chat_ids:
            try:
                member = await self._bot.get_chat_member(chat_id, user_id)
            except TelegramAPIError as exc:
                return QualificationState.UNKNOWN, "unavailable", type(exc).__name__
            status = str(member.status)
            statuses.append(f"{chat_id}:{status}")
            if not _is_member(member):
                return QualificationState.INELIGIBLE, ";".join(statuses), None
        return QualificationState.ELIGIBLE, ";".join(statuses), None

    async def _trigger_full_draw(self, campaign: object) -> None:
        rules = CampaignRules.model_validate(campaign.rules)
        if rules.draw_mode != "full" or rules.participant_target is None:
            return
        count = await self._queries.eligible_participant_count(campaign.id)
        if count < rules.participant_target:
            return
        await self._campaigns.early_cutoff(
            campaign.id,
            actor_user_id=campaign.created_by,
            cutoff_at=utcnow(),
            reason=f"满人开奖达到 {count}/{rules.participant_target}",
        )


def member_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="我的票数", callback_data="member:tickets")],
            [InlineKeyboardButton(text="邀请记录", callback_data="member:invites")],
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


def _is_member(member: object) -> bool:
    status = str(getattr(member, "status", ""))
    if status in {"member", "administrator", "creator"}:
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))


def _extended_tips(campaign: object) -> bool:
    presentation = getattr(campaign, "presentation", {}) or {}
    return bool(presentation.get("message_tips", True))
