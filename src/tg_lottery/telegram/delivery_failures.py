"""Recognize connection failures before a single Telegram write can start."""

from aiogram.exceptions import TelegramNetworkError
from aiohttp import ClientConnectorError

SINGLE_INVITE_OPERATIONS = frozenset({"create_invite_link", "create_required_chat_invite"})
LEGACY_CONNECT_ERROR_PREFIX = (
    "HTTP Client says - ClientConnectorError: Cannot connect to host api.telegram.org:443 "
)


def failed_before_invite_request(operation: str, error: Exception) -> bool:
    # aiogram preserves aiohttp's exception as __cause__; text alone is not live evidence.
    return (
        operation in SINGLE_INVITE_OPERATIONS
        and isinstance(error, TelegramNetworkError)
        and isinstance(error.__cause__, ClientConnectorError)
    )
