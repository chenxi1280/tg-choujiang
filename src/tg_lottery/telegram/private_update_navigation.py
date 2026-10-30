from tg_lottery.domain.errors import DomainError
from tg_lottery.telegram import command_input
from tg_lottery.telegram.start_payloads import is_unpublished_preview


async def private_message(handler, message):
    if message.from_user is None or getattr(message.from_user, "is_bot", False):
        return
    text = (message.text or "").strip()
    try:
        command = command_input.parse_bot_command(text)
        await project_private_start(handler, message, command=command)
        if should_suspend(command, bot_username=handler._bot_username):
            await handler._admin_flow.suspend_draft(message.from_user.id)
        if await lottery_message(handler, message, text=text, command=command):
            return
        if await handler._private_navigation(message, text):
            return
        if await handler._personal_flow.handle_command(message, text):
            return
        if not await handler._admin_flow.handle_text(message):
            await handler._personal_flow.show_index(message)
    except (DomainError, ValueError) as exc:
        await handler._send_operation_error(message, exc)


def should_suspend(command, *, bot_username):
    return (
        command is not None
        and command.addressed_to(bot_username)
        and command.name in {"/start", "/menu", "/help", "/region", "/myid", "/join", "/index"}
        and not (command.name == "/start" and is_unpublished_preview(command.argument))
    )


async def project_private_start(handler, message, *, command):
    relationships = getattr(handler._member_flow, "_relationships", None)
    if (
        relationships is None
        or command is None
        or getattr(message, "forward_origin", None) is not None
    ):
        return
    if command.name == "/start" and command.addressed_to(handler._bot_username):
        await relationships.record(message.from_user.id, message.date, started=True)


async def read_callback(handler, query, *, data):
    from tg_lottery.telegram.channel_shortcuts import handle_shortcut

    if data.startswith("cp:") and await handle_shortcut(handler._navigation, query):
        return True
    if data.startswith("lq:") and await handler._lottery_queries.callback(query):
        return True
    if await handler._reputation.callback(query):
        return True
    if await handler._navigation.handle_route(query, data=data, personal=handler._personal_flow):
        return True
    return await handler._personal_flow.handle_callback(query, data)


async def send_operation_error(handler, message, exc):
    from tg_lottery.telegram.panel_edit import PanelUnavailableError
    from tg_lottery.telegram.public_projection import public_error_text

    handler._logger.warning(
        "Telegram operation rejected",
        extra={"error_type": type(exc).__name__},
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    text = public_error_text(exc, handler._default_timezone)
    prefix = "页面显示未完成" if isinstance(exc, PanelUnavailableError) else "操作未完成"
    await message.answer(f"{prefix}: {text}")


async def callback_error(handler, query, exc):
    from aiogram.types import Message

    from tg_lottery.telegram import member_callbacks
    from tg_lottery.telegram.public_projection import public_error_text

    message = query.message
    if isinstance(message, Message) and message.chat.type == "private":
        await handler._send_operation_error(message, exc)
        return
    if await member_callbacks.send_bonus_navigation_reminder(handler._member_flow, query, exc):
        return
    await member_callbacks.safe_answer(
        query, public_error_text(exc, handler._default_timezone), alert=True
    )


async def lottery_message(handler, message, *, text, command):
    if (
        command
        and command.addressed_to(handler._bot_username)
        and command.name == "/start"
        and command.argument == "myprogress"
    ):
        text = "抽奖进度"
    from tg_lottery.telegram.lottery_queries import query_kind

    if query_kind(text, handler._bot_username, private=True) is None:
        return False
    return await handler._lottery_queries.message(message, text)
