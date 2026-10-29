"""Recognize connection failures before a single Telegram write can start."""

from aiohttp import ClientConnectorError
from sqlalchemy import func, select

from tg_lottery.domain.enums import DeliveryCertainty
from tg_lottery.infrastructure.db import DeliveryAttempt
from tg_lottery.telegram.single_request_session import PreRequestConnectionError

SINGLE_INVITE_OPERATIONS = frozenset({"create_invite_link", "create_required_chat_invite"})
SINGLE_NOTICE_OPERATIONS = frozenset(
    {
        "send_progress_notice",
        "send_temporary_reminder",
        "delete_message",
        "send_featured_campaign",
        "send_featured_digest",
        "edit_featured_campaign",
    }
)
SINGLE_CONNECT_OPERATIONS = SINGLE_INVITE_OPERATIONS | SINGLE_NOTICE_OPERATIONS
_NOTICE_CONNECT_RETRY_DELAYS = (5, 15, 30, 60, 90)
LEGACY_CONNECT_ERROR_PREFIX = (
    "HTTP Client says - ClientConnectorError: Cannot connect to host api.telegram.org:443 "
)


def failed_before_invite_request(operation: str, error: Exception) -> bool:
    return operation in SINGLE_INVITE_OPERATIONS and _connector_error(error)


def failed_before_single_request(operation: str, error: Exception) -> bool:
    return operation in SINGLE_CONNECT_OPERATIONS and _connector_error(error)


async def classify_connection_failure(sessions, outbox, error) -> tuple[bool, int | None]:
    if not failed_before_single_request(outbox.operation, error):
        return False, None
    if outbox.operation not in SINGLE_NOTICE_OPERATIONS:
        return True, None
    async with sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(DeliveryAttempt)
            .where(
                DeliveryAttempt.outbox_id == outbox.id,
                DeliveryAttempt.certainty == DeliveryCertainty.EXPLICIT_REJECTION,
                DeliveryAttempt.error_code == PreRequestConnectionError.__name__,
            )
        )
    previous_failures = int(count or 0)
    if previous_failures >= len(_NOTICE_CONNECT_RETRY_DELAYS):
        return True, None
    return True, _NOTICE_CONNECT_RETRY_DELAYS[previous_failures]


def _connector_error(error: Exception) -> bool:
    # Only our non-redirecting session proves that this was the first HTTP request.
    return isinstance(error, PreRequestConnectionError) and isinstance(
        error.__cause__, ClientConnectorError
    )
