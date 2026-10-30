import logging

from aiogram import Bot
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    Message,
    Update,
)

from tg_lottery.application.activity import ActivityService
from tg_lottery.application.callback_tokens import CallbackTokenService
from tg_lottery.application.groups import GroupService, TelegramAdmin
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import (
    AppliedDraftCallback,
    DomainError,
    RecoverableDraftCallback,
    RecoverableMemberCallback,
)
from tg_lottery.telegram import command_input, member_callbacks
from tg_lottery.telegram.admin_flow import AdminFlow
from tg_lottery.telegram.bot_membership import sync_bot_membership
from tg_lottery.telegram.callback_feedback import callback_feedback
from tg_lottery.telegram.group_reputation import GroupReputation
from tg_lottery.telegram.help_views import DRAFT_UNAVAILABLE_TEXT
from tg_lottery.telegram.inline_sharing import answer_inline_share
from tg_lottery.telegram.lottery_queries import LotteryQueries
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.message_observation import observe_message
from tg_lottery.telegram.personal_flow import PersonalFlow
from tg_lottery.telegram.start_payloads import dispatch_handler_start
from tg_lottery.telegram.ui_style import row_keyboard


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
        stats: MessageStatsService,
        bot_username: str = "",
        navigation,
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
        self._stats = stats
        self._bot_username = bot_username
        self._navigation = navigation
        self._logger = logging.getLogger(__name__)
        self._reputation = GroupReputation(queries=queries, bot_username=bot_username)
        self._lottery_queries = LotteryQueries(queries, bot_username=bot_username)

    async def handle(self, update: Update) -> None:
        if update.inline_query:
            await answer_inline_share(
                self._bot,
                update.inline_query,
                queries=self._queries,
                bot_username=self._bot_username,
            )
            return
        if update.callback_query:
            await self._callback(update.callback_query)
            return
        if update.chat_join_request:
            await self._join_request(update.chat_join_request)
            return
        if update.chat_member:
            await self._chat_member(update.chat_member)
            return
        if update.my_chat_member and not update.my_chat_member.chat.is_direct_messages:
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
        text = (message.text or "").strip()
        if await self._lottery_queries.message(message, text):
            if message.from_user is not None and not text.startswith("/"):
                await self._record_message_activity(message)
            return
        if message.from_user is None:
            return
        if text.startswith("/"):
            await self._group_command(message, text)
            return
        if await self._reputation.message(message, text):
            return
        await self._record_message_activity(message)

    async def _record_message_activity(self, message):
        observation = observe_message(message)
        await self._stats.record(observation)
        await self._activity.record_message(observation)

    async def _group_command(self, message: Message, text: str) -> None:
        if text.split(maxsplit=1)[0].partition("@")[0] not in command_input.CHINESE_COMMANDS:
            return
        command = command_input.parse_bot_command(text)
        if not command.addressed_to(self._bot_username):
            return
        name = command.name
        if name == "/getid":
            await message.answer(
                f"本群 ID：{message.chat.id}（以 -100 开头为超级群/频道 ID，可直接复制使用）"
            )
            return
        if name in command_input.PRIVATE_BOT_COMMANDS:
            await message.answer(
                "该命令请在私聊中使用。",
                reply_markup=row_keyboard(
                    [
                        [
                            InlineKeyboardButton(
                                text="私聊我",
                                url=f"https://t.me/{self._bot_username}",
                            )
                        ]
                    ]
                ),
            )

    async def _private_message(self, message: Message) -> None:
        from tg_lottery.telegram.private_update_navigation import private_message

        await private_message(self, message)

    async def _private_navigation(self, message: Message, text: str) -> bool:
        command = command_input.parse_bot_command(text)
        if command is None:
            return False
        if not command.addressed_to(self._bot_username):
            return True
        if command.name == "/start":
            if not await self._start_payload(message, command.argument):
                await self._personal_flow.show_index(message)
            return True
        handlers = {
            "/menu": self._personal_flow.show_index,
            "/new": self._admin_flow.start_create_message,
        }
        handler = handlers.get(command.name)
        if handler is None:
            return False
        await handler(message)
        return True

    async def _start_payload(self, message: Message, payload: str) -> bool:
        return await dispatch_handler_start(self, message, payload)

    async def _callback(self, query: CallbackQuery) -> None:
        data = query.data or ""
        async with callback_feedback(
            query,
            self._logger,
            deferred=data.startswith(command_input.DEFERRED_CALLBACK_PREFIXES),
        ):
            if not isinstance(query.message, Message):
                await member_callbacks.safe_answer(
                    query, "原页面已失效, 请发送 /首页 重新打开。", alert=True
                )
                return
            try:
                if data.startswith(("nav:", "m:", "member:")) and data not in {
                    "nav:create",
                    "nav:new",
                }:
                    await self._admin_flow.suspend_draft(query.from_user.id)
                await self._dispatch_callback(query, data)
            except (DomainError, ValueError) as exc:
                await self._callback_error(query, exc)

    async def _callback_error(self, query: CallbackQuery, exc: Exception) -> None:
        from tg_lottery.telegram.private_update_navigation import callback_error

        await callback_error(self, query, exc)

    async def _send_operation_error(self, message: Message, exc: Exception) -> None:
        from tg_lottery.telegram.private_update_navigation import send_operation_error

        await send_operation_error(self, message, exc)

    async def _dispatch_callback(self, query: CallbackQuery, data: str) -> None:
        if data == "preview:unpublished":
            await member_callbacks.safe_answer(query, "这是预览，活动尚未发布。", alert=True)
            return
        message = query.message
        if not isinstance(message, Message):
            return
        from tg_lottery.telegram.private_update_navigation import read_callback

        if await read_callback(self, query, data=data):
            return
        if data == "nav:new":
            await self._admin_flow.start_new(query)
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
            return
        await member_callbacks.safe_answer(
            query, "此按钮已失效, 请发送 /首页 重新打开当前页面。", alert=True
        )

    async def _dispatch_member_callback(self, query: CallbackQuery, data: str) -> bool:
        message = query.message
        if not isinstance(message, Message):
            return False
        if await self._dispatch_card_callback(query, data):
            return True
        if await member_callbacks.record_callback(
            self._member_flow, self._personal_flow, query, data=data
        ):
            return True
        if data == "member:noop":
            return True
        if await member_callbacks.bonus_navigation_callback(self._member_flow, query, data):
            return True
        if data.startswith("member:invite_group:"):
            await member_callbacks.group_invite_callback(
                self._member_flow, query, data.removeprefix("member:invite_group:")
            )
            return True
        if data.startswith("member:invite_page:"):
            await member_callbacks.invite_page_callback(
                self._member_flow, query, data.removeprefix("member:invite_page:")
            )
            return True
        if data.startswith("member:invite:"):
            await member_callbacks.invite_link_callback(
                self._member_flow, query, data.removeprefix("member:invite:")
            )
            return True
        if data.startswith("member:verify:"):
            await self._member_flow.register(
                message,
                data.removeprefix("member:verify:"),
                user=query.from_user,
                observed_at=utcnow(),
                callback=query,
            )
            return True
        return await self._dispatch_member_page_callback(query, data)

    async def _dispatch_member_page_callback(self, query: CallbackQuery, data: str) -> bool:
        message = query.message
        if not isinstance(message, Message):
            return False
        if data.startswith("member:missing:"):
            payload = data.removeprefix("member:missing:")
            reference, separator, page = payload.rpartition(":")
            if not separator or not page.isdecimal():
                raise ValueError("invalid missing targets callback")
            await self._navigation.handle_route(
                query,
                data=f"m:conditions:{reference}:{max(0, int(page) - 1)}",
                personal=self._personal_flow,
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

    async def _dispatch_card_callback(self, query: CallbackQuery, data: str) -> bool:
        if data.startswith("member:join:"):
            await self._member_flow.register_callback(query, data.removeprefix("member:join:"))
            return True
        if data.startswith("member:bonus:"):
            await self._member_flow.bonus_callback(query, data.removeprefix("member:bonus:"))
            return True
        if data.startswith("member:rules:"):
            await self._member_flow.rules_callback(query, data.removeprefix("member:rules:"))
            return True
        return False

    async def _dispatch_secure_callback(self, query: CallbackQuery, data: str) -> None:
        message = query.message
        if not isinstance(message, Message):
            return
        try:
            grant = await self._tokens.consume(
                data.removeprefix("secure:"),
                owner_user_id=query.from_user.id,
                chat_id=message.chat.id,
            )
        except AppliedDraftCallback:
            await self._admin_flow.recover_applied_draft_callback(query)
            return
        except RecoverableMemberCallback as exc:
            await member_callbacks.safe_answer(query, str(exc), alert=True)
            await self._navigation.recover(query, exc.payload, personal=self._personal_flow)
            return
        except RecoverableDraftCallback:
            recovered = await self._admin_flow.recover_draft_callback(query)
            text = "请使用最新草稿面板或下方继续按钮" if recovered else DRAFT_UNAVAILABLE_TEXT
            await member_callbacks.safe_answer(query, text, alert=True)
            return
        if not grant.action.startswith("draft_"):
            await self._admin_flow.suspend_draft(query.from_user.id)
        if grant.action in {"member:navigate", "member:region_set"}:
            await self._navigation.handle_grant(query, grant, personal=self._personal_flow)
            return
        await self._admin_flow.handle_grant(query, grant)

    async def _join_request(self, event: object) -> None:
        invite = getattr(event, "invite_link", None)
        user = getattr(event, "from_user", None)
        if user is None:
            return
        if invite is not None:
            await self._record_invitation_join_request(
                invite, user, event.date, chat_id=event.chat.id
            )
        campaign_ids = await self._queries.campaigns_for_mandatory_chat(event.chat.id)
        for campaign_id in campaign_ids:
            await self._member_flow.join_request_pending(
                campaign_id=campaign_id,
                user=user,
                chat_id=event.chat.id,
                observed_at=event.date,
            )

    async def _record_invitation_join_request(
        self, invite: object, user: object, observed_at: object, *, chat_id: int
    ) -> None:
        link = await self._queries.invite_link_by_url(invite.invite_link)
        if link is None:
            return
        await self._invitations.record_join_request(
            JoinObservation(
                link.id,
                user.id,
                observed_at,
                is_bot=user.is_bot,
                chat_id=chat_id,
                display_name=user.full_name,
            )
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
                    JoinObservation(
                        link.id,
                        user.id,
                        event.date,
                        is_bot=user.is_bot,
                        chat_id=event.chat.id,
                        display_name=user.full_name,
                    )
                )
        invitations = await self._queries.invitations_for_chat_user(event.chat.id, user.id)
        for invitation in invitations:
            await self._invitations.record_rejoined(
                invitation.campaign_id,
                user.id,
                event.date,
                participation_group_id=invitation.participation_group_id,
            )
        await self._project_participant_membership(event, is_member=True)

    async def _record_member_leave(self, event: ChatMemberUpdated) -> None:
        user = event.new_chat_member.user
        invitations = await self._queries.invitations_for_chat_user(event.chat.id, user.id)
        for invitation in invitations:
            await self._invitations.record_left(
                invitation.campaign_id,
                user.id,
                event.date,
                participation_group_id=invitation.participation_group_id,
            )
        await self._project_participant_membership(event, is_member=False)

    async def _project_participant_membership(
        self, event: ChatMemberUpdated, *, is_member: bool
    ) -> None:
        campaign_ids = await self._queries.campaigns_for_mandatory_chat(event.chat.id)
        for campaign_id in campaign_ids:
            await self._member_flow.membership_changed(
                campaign_id=campaign_id,
                user=event.new_chat_member.user,
                chat_id=event.chat.id,
                is_member=is_member,
                observed_at=event.date,
            )

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
        await sync_bot_membership(self, event)


def _is_member(member: object) -> bool:
    status = str(getattr(member, "status", ""))
    if status in {"member", "administrator", "creator"}:
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))


def _is_admin(member: object) -> bool:
    return str(getattr(member, "status", "")) in {"administrator", "creator"}
