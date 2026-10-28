from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import DeliveryAttempt, TelegramOutbox


def delivery_log_context(outbox: TelegramOutbox, attempt_number: int) -> dict[str, object]:
    return {
        "outbox_id": str(outbox.id),
        "operation": outbox.operation,
        "attempt_number": attempt_number,
    }


def sanitized_remote_result(remote_result: dict[str, object]) -> dict[str, object]:
    sanitized = dict(remote_result)
    sanitized.pop("invite_link", None)
    return sanitized


async def state_after_explicit_failure(session: AsyncSession, row: TelegramOutbox) -> OutboxState:
    prior_unknown = await session.scalar(
        select(DeliveryAttempt.id).where(
            DeliveryAttempt.outbox_id == row.id,
            DeliveryAttempt.certainty == DeliveryCertainty.UNKNOWN,
        )
    )
    return OutboxState.UNKNOWN_HOLD if prior_unknown is not None else OutboxState.PRE_ACCEPT_FAILED


def add_delivery_attempt(
    session: AsyncSession,
    row: TelegramOutbox,
    *,
    attempt_number: int,
    certainty: DeliveryCertainty,
    error_code: str | None = None,
    error_description: str | None = None,
    remote_result: dict[str, object] | None = None,
) -> None:
    session.add(
        DeliveryAttempt(
            outbox_id=row.id,
            attempt_number=attempt_number,
            telegram_method=row.operation,
            certainty=certainty,
            error_code=error_code,
            error_description=error_description,
            remote_result=remote_result,
            finished_at=utcnow(),
        )
    )
