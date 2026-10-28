"""Recognize connection failures before a single Telegram write can start."""

from aiohttp import ClientConnectorError

from tg_lottery.telegram.single_request_session import PreRequestConnectionError

SINGLE_INVITE_OPERATIONS = frozenset({"create_invite_link", "create_required_chat_invite"})
SINGLE_NOTICE_OPERATIONS = frozenset(
    {"send_progress_notice", "send_temporary_reminder", "delete_message"}
)
SINGLE_CONNECT_OPERATIONS = SINGLE_INVITE_OPERATIONS | SINGLE_NOTICE_OPERATIONS
_NOTICE_CONNECT_RETRY_DELAYS = (5, 15)
LEGACY_CONNECT_ERROR_PREFIX = (
    "HTTP Client says - ClientConnectorError: Cannot connect to host api.telegram.org:443 "
)


def failed_before_invite_request(operation: str, error: Exception) -> bool:
    return operation in SINGLE_INVITE_OPERATIONS and _connector_error(error)


def failed_before_single_request(operation: str, error: Exception) -> bool:
    return operation in SINGLE_CONNECT_OPERATIONS and _connector_error(error)


def notice_connection_retry_delay(operation: str, attempt_number: int) -> int | None:
    if operation not in SINGLE_NOTICE_OPERATIONS or not 1 <= attempt_number <= len(
        _NOTICE_CONNECT_RETRY_DELAYS
    ):
        return None
    return _NOTICE_CONNECT_RETRY_DELAYS[attempt_number - 1]


def _connector_error(error: Exception) -> bool:
    # Only our non-redirecting session proves that this was the first HTTP request.
    return isinstance(error, PreRequestConnectionError) and isinstance(
        error.__cause__, ClientConnectorError
    )
