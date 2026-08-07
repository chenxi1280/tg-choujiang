from datetime import datetime
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from tg_lottery.application.participants import (
    ParticipantService,
    QualificationResult,
    RegistrationAttempt,
    RegistrationRequest,
)
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState


class MemberFlow:
    def __init__(
        self,
        *,
        bot: Bot,
        participants: ParticipantService,
        queries: QueryService,
    ) -> None:
        self._bot = bot
        self._participants = participants
        self._queries = queries

    async def register(
        self,
        message: Message,
        campaign_id: UUID,
        *,
        user: User | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        campaign = await self._queries.campaign(campaign_id)
        if campaign is None:
            raise ValueError("活动不存在或已经失效")
        user = user or _required_user(message)
        attempt = await self._participants.begin_registration(
            _registration_request(campaign.id, user, observed_at or message.date)
        )
        state = attempt.state
        if attempt.requires_observation:
            state = await self._complete_observation(attempt, user.id)
        if state is QualificationState.ELIGIBLE:
            await message.answer(
                "报名成功。专属邀请链接创建并确认后会发送到这里。",
                reply_markup=member_keyboard(),
            )
            return
        verify = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="验证资格",
                        callback_data=f"member:verify:{campaign.id}",
                    )
                ]
            ]
        )
        if state is QualificationState.INELIGIBLE:
            await message.answer("尚未满足频道关注条件。完成后请重新验证。", reply_markup=verify)
        else:
            await message.answer("当前无法核验频道资格，状态保持待核验。", reply_markup=verify)

    async def _complete_observation(
        self, attempt: RegistrationAttempt, user_id: int
    ) -> QualificationState:
        state, status, error = await self._qualification(attempt.required_channel_id, user_id)
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
        self, channel_id: int | None, user_id: int
    ) -> tuple[QualificationState, str, str | None]:
        if channel_id is None:
            return QualificationState.ELIGIBLE, "not_required", None
        try:
            member = await self._bot.get_chat_member(channel_id, user_id)
        except TelegramAPIError as exc:
            return QualificationState.UNKNOWN, "unavailable", type(exc).__name__
        status = str(member.status)
        state = QualificationState.ELIGIBLE if _is_member(member) else QualificationState.INELIGIBLE
        return state, status, None


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
