"""Recognize definite rejection of a UI reply to this update's source only."""

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    AnswerInlineQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    SendDocument,
    SendMessage,
    SendPhoto,
)

_UI_METHODS = (SendMessage, SendPhoto, SendDocument, EditMessageText, EditMessageReplyMarkup)
_FORBIDDEN_REASONS = frozenset(
    {
        "Forbidden: bot was blocked by the user",
        "Forbidden: user is deactivated",
        "Forbidden: bot was kicked from the supergroup chat",
        "Forbidden: bot was kicked from the group chat",
        "Forbidden: bot is not a member of the supergroup chat",
        "Forbidden: bot is not a member of the channel chat",
    }
)
_BAD_REQUEST_REASONS = frozenset(
    {
        "Bad Request: chat not found",
        "Bad Request: not enough rights to send text messages to the chat",
        "Bad Request: CHAT_WRITE_FORBIDDEN",
    }
)
_EXPIRED_INLINE_REASONS = frozenset(
    {
        "Bad Request: query is too old and response timeout expired or query ID is invalid",
        "Bad Request: QUERY_ID_INVALID",
    }
)


def rejected_source_response(update, error: Exception) -> bool:
    if not isinstance(error, (TelegramForbiddenError, TelegramBadRequest)):
        return False
    if isinstance(error.method, AnswerInlineQuery):
        return _expired_inline_response(update, error)
    reasons = (
        _FORBIDDEN_REASONS if isinstance(error, TelegramForbiddenError) else _BAD_REQUEST_REASONS
    )
    if error.message not in reasons or not isinstance(error.method, _UI_METHODS):
        return False
    targets = set()
    for event in (update.message, update.my_chat_member):
        if event is not None:
            targets.add(event.chat.id)
    query = update.callback_query
    if query is not None:
        targets.add(query.from_user.id)
        if query.message is not None:
            targets.add(query.message.chat.id)
    return error.method.chat_id in targets


def _expired_inline_response(update, error):
    query = update.inline_query
    return bool(
        isinstance(error, TelegramBadRequest)
        and error.message in _EXPIRED_INLINE_REASONS
        and query is not None
        and error.method.inline_query_id == query.id
    )
