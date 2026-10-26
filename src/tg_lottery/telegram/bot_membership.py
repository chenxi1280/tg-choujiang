"""Project bot membership without replaying inaccessible historical promotions."""

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import GetChatAdministrators

from tg_lottery.application.groups import TelegramAdmin
from tg_lottery.application.records import utcnow
from tg_lottery.telegram.bot_permission_reminders import send_bot_permission_reminder

_MEMBERSHIP_LOST = frozenset(
    {
        "Forbidden: bot was kicked from the supergroup chat",
        "Forbidden: bot was kicked from the group chat",
        "Forbidden: bot is not a member of the supergroup chat",
        "Forbidden: bot is not a member of the channel chat",
    }
)


class BotMembershipUnavailable(RuntimeError):
    """Current access was explicitly lost and cached administrators were revoked."""


async def sync_bot_membership(handler, event):
    if getattr(event.chat, "is_direct_messages", False):
        return
    if event.chat.type == "private":
        relationships = getattr(handler._member_flow, "_relationships", None)
        if relationships is not None:
            await relationships.record(
                event.chat.id,
                event.date,
                blocked=str(event.new_chat_member.status) in {"kicked", "left"},
            )
        return
    if str(event.new_chat_member.status) not in {"administrator", "creator"}:
        await handler._groups.deactivate_group(event.chat.id, event.date)
        await send_bot_permission_reminder(handler._bot, event)
        return
    admins = await _current_admins(handler, event)
    records = tuple(
        TelegramAdmin(
            user_id=admin.user.id,
            source="owner" if str(admin.status) == "creator" else "telegram_admin",
        )
        for admin in admins
        if not admin.user.is_bot
    )
    await handler._groups.sync_group(
        chat_id=event.chat.id,
        title=_public_chat_title(event.chat.title),
        timezone=handler._default_timezone,
        permissions=event.new_chat_member.model_dump(mode="json", exclude_none=True),
        admins=records,
        observed_at=event.date,
    )
    await send_bot_permission_reminder(handler._bot, event)


async def _current_admins(handler, event):
    try:
        return await handler._bot.get_chat_administrators(event.chat.id)
    except TelegramForbiddenError as exc:
        if not (
            isinstance(exc.method, GetChatAdministrators)
            and exc.method.chat_id == event.chat.id
            and exc.message in _MEMBERSHIP_LOST
        ):
            raise
        # Revoke permissions first. A failed database write must remain retryable.
        await handler._groups.deactivate_group(event.chat.id, utcnow())
        raise BotMembershipUnavailable(
            f"Bot membership unavailable: chat={event.chat.id}; "
            f"cached administrators revoked; {exc.message}"
        ) from exc


def _public_chat_title(title: object) -> str:
    value = str(title).strip() if title is not None else ""
    return value or "未命名群组/频道"
