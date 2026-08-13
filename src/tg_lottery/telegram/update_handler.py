import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, Update

from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.callback_tokens import CallbackTokenService
from tg_lottery.application.groups import GroupService, TelegramAdmin
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import DomainError
from tg_lottery.telegram.admin_flow import AdminFlow
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.personal_flow import PersonalFlow
from tg_lottery.telegram.public_projection import public_error_text


class LotteryUpdateHandler:
    def __init__(
        self,
        *,
        bot: Bot,
        default_timezone: str,
        admin_flow: AdminFlow,
        member_flow: MemberFlow,
        personal_flow: PersonalFlow,
        tokens: CallbackTokenService,
        groups: GroupService,
        activity: ActivityService,
        invitations: InvitationService,
        queries: QueryService,
    ) -> None:
        self._bot = bot
        self._default_timezone = default_timezone
        self._admin_flow = admin_flow
        self._member_flow = member_flow
        self._personal_flow = personal_flow
        self._tokens = tokens
        self._groups = groups
        self._activity = activity
        self._invitations = invitations
        self._queries = queries
        self._logger = logging.getLogger(__name__)

    async def handle(self, update: Update) -> None:
        if update.callback_query:
            await self._callback(update.callback_query)
            return
        if update.chat_join_request:
            await self._join_request(update.chat_join_request)
            return
        if update.chat_member:
            await self._chat_member(update.chat_member)
            return
        if update.my_chat_member:
            await self._my_chat_member(update.my_chat_member)
            return
        if update.message:
            await self._message(update.message)

    async def _message(self, message: Message) -> None:
        if message.migrate_to_chat_id is not None:
            await self._groups.migrate_group(message.chat.id, message.migrate_to_chat_id)
            return
        if message.chat.type == "private":
            await self._private_message(message)
            return
        if message.from_user is None:
            return
        await self._activity.record_message(
            MessageObservation(
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=message.from_user.id,
                event_at=message.date,
                text=message.text or message.caption,
                is_bot=message.from_user.is_bot,
                is_service=message.text is None and message.caption is None,
                is_forwarded=message.forward_origin is not None,
            )
        )

    async def _private_message(self, message: Message) -> None:
        text = (message.text or "").strip()
        try:
            if text.startswith("/start"):
                payload = text.partition(" ")[2]
                if not await self._start_payload(message, payload):
                    await self._personal_flow.show_guide(message)
                return
            if text == "/menu":
                await self._admin_flow.show_main(message)
                return
            if text == "/new":
                await self._admin_flow.start_create_message(message)
                return
            if await self._personal_flow.handle_command(message, text):
                return
            if not await self._admin_flow.handle_text(message):
                await self._admin_flow.show_main(message)
        except (DomainError, ValueError) as exc:
            await self._send_operation_error(message, exc)

    async def _start_payload(self, message: Message, payload: str) -> bool:
        if payload.startswith("join_"):
            await self._member_flow.register(message, payload.removeprefix("join_"))
            return True
        if payload.startswith("rules_"):
            await self._member_flow.show_rules(message, payload.removeprefix("rules_"))
            return True
        if not payload.startswith("condition_"):
            return False
        reference, separator, position = payload.removeprefix("condition_").rpartition("_")
        if not separator or not position.isdecimal():
            raise ValueError("invalid participation condition payload")
        await self._member_flow.show_condition(message, reference, int(position))
        return True

    async def _callback(self, query: CallbackQuery) -> None:
        await self._answer_callback(query)
        if not isinstance(query.message, Message):
            return
        try:
            await self._dispatch_callback(query, query.data or "")
        except (DomainError, ValueError) as exc:
            await self._send_operation_error(query.message, exc)

    async def _send_operation_error(self, message: Message, exc: Exception) -> None:
        self._logger.warning(
            "Telegram operation rejected",
            extra={"error_type": type(exc).__name__},
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        text = public_error_text(exc, self._default_timezone)
        await message.answer(f"操作未完成: {text}")

    async def _dispatch_callback(self, query: CallbackQuery, data: str) -> None:
        message = query.message
        if not isinstance(message, Message):
            return
        if await self._personal_flow.handle_callback(query, data):
            return
        if data == "nav:create":
            await self._admin_flow.start_create(query)
            return
        if data == "nav:campaigns":
            await self._admin_flow.show_campaigns(query)
            return
        if data.startswith("nav:campaign:"):
            await self._admin_flow.show_campaign_reference(
                query, data.removeprefix("nav:campaign:")
            )
            return
        if await self._dispatch_member_callback(query, data):
            return
        if data.startswith("secure:"):
            await self._dispatch_secure_callback(query, data)

    async def _dispatch_member_callback(self, query: CallbackQuery, data: str) -> bool:
        message = query.message
        if not isinstance(message, Message):
            return False
        if data == "member:tickets":
            await self._member_flow.show_tickets(message, query.from_user.id)
            return True
        if data == "member:invites":
            await self._member_flow.show_invitations(message, query.from_user.id)
            return True
        if data.startswith("member:verify:"):
            await self._member_flow.register(
                message,
                data.removeprefix("member:verify:"),
                user=query.from_user,
                observed_at=utcnow(),
            )
            return True
        if data.startswith("member:condition:"):
            payload = data.removeprefix("member:condition:")
            reference, separator, position = payload.rpartition(":")
            if not separator or not position.isdecimal():
                raise ValueError("invalid participation condition callback")
            await self._member_flow.show_condition(message, reference, int(position))
            return True
        return False

    async def _dispatch_secure_callback(self, query: CallbackQuery, data: str) -> None:
        message = query.message
        if not isinstance(message, Message):
            return
        grant = await self._tokens.consume(
            data.removeprefix("secure:"),
            owner_user_id=query.from_user.id,
            chat_id=message.chat.id,
        )
        await self._admin_flow.handle_grant(query, grant)

    async def _answer_callback(self, query: CallbackQuery) -> None:
        try:
            await query.answer()
        except TelegramBadRequest as exc:
            self._logger.warning(
                "callback acknowledgement rejected by Telegram",
                extra={"callback_query_id": query.id, "error": str(exc)},
            )

    async def _join_request(self, event: object) -> None:
        invite = getattr(event, "invite_link", None)
        user = getattr(event, "from_user", None)
        if invite is None or user is None:
            return
        link = await self._queries.invite_link_by_url(invite.invite_link)
        if link is None:
            return
        await self._invitations.record_join_request(
            JoinObservation(link.id, user.id, event.date, is_bot=user.is_bot)
        )

    async def _chat_member(self, event: ChatMemberUpdated) -> None:
        was_member = _is_member(event.old_chat_member)
        is_member = _is_member(event.new_chat_member)
        if not was_member and is_member:
            await self._record_member_join(event)
        if was_member and not is_member:
            await self._record_member_leave(event)
        await self._sync_admin_change(event)

    async def _record_member_join(self, event: ChatMemberUpdated) -> None:
        user = event.new_chat_member.user
        if event.invite_link:
            link = await self._queries.invite_link_by_url(event.invite_link.invite_link)
            if link:
                await self._invitations.record_joined(
                    JoinObservation(link.id, user.id, event.date, is_bot=user.is_bot)
                )
        invitations = await self._queries.invitations_for_chat_user(event.chat.id, user.id)
        for invitation in invitations:
            await self._invitations.record_rejoined(invitation.campaign_id, user.id, event.date)

    async def _record_member_leave(self, event: ChatMemberUpdated) -> None:
        user = event.new_chat_member.user
        invitations = await self._queries.invitations_for_chat_user(event.chat.id, user.id)
        for invitation in invitations:
            await self._invitations.record_left(invitation.campaign_id, user.id, event.date)

    async def _sync_admin_change(self, event: ChatMemberUpdated) -> None:
        old_admin = _is_admin(event.old_chat_member)
        new_admin = _is_admin(event.new_chat_member)
        if old_admin == new_admin:
            return
        status = str(event.new_chat_member.status)
        await self._groups.sync_admin(
            chat_id=event.chat.id,
            admin=TelegramAdmin(
                user_id=event.new_chat_member.user.id,
                source="owner" if status == "creator" else "telegram_admin",
            ),
            active=new_admin,
        )

    async def _my_chat_member(self, event: ChatMemberUpdated) -> None:
        if not _is_admin(event.new_chat_member):
            await self._groups.deactivate_group(event.chat.id, event.date)
            return
        admins = await self._bot.get_chat_administrators(event.chat.id)
        records = tuple(
            TelegramAdmin(
                user_id=admin.user.id,
                source="owner" if str(admin.status) == "creator" else "telegram_admin",
            )
            for admin in admins
            if not admin.user.is_bot
        )
        permissions = event.new_chat_member.model_dump(mode="json", exclude_none=True)
        await self._groups.sync_group(
            chat_id=event.chat.id,
            title=_public_chat_title(event.chat.title),
            timezone=self._default_timezone,
            permissions=permissions,
            admins=records,
            observed_at=event.date,
        )


def _is_member(member: object) -> bool:
    status = str(getattr(member, "status", ""))
    if status in {"member", "administrator", "creator"}:
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))


def _public_chat_title(title: object) -> str:
    value = str(title).strip() if title is not None else ""
    return value or "未命名群组/频道"


def _is_admin(member: object) -> bool:
    return str(getattr(member, "status", "")) in {"administrator", "creator"}
