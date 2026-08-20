from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.records import add_audit, add_outbox
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import TelegramOutbox

_CLEANUP_LOCK_KEY = "legacy_draw_commitment_notice_cleanup"
_SOURCE_OPERATION = "send_draw_commitment"
_DELETE_OPERATION = "delete_message"


async def queue_legacy_commitment_notice_cleanup(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session, session.begin():
        await _lock_cleanup(session)
        sources = await _confirmed_commitment_notices(session)
        existing_keys = await _existing_cleanup_keys(session, sources)
        queued = 0
        for source in sources:
            business_key = _cleanup_key(source)
            if business_key in existing_keys:
                continue
            _queue_delete(session, source, business_key)
            queued += 1
        return queued


async def _lock_cleanup(session: AsyncSession) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(_CLEANUP_LOCK_KEY))))


async def _confirmed_commitment_notices(session: AsyncSession) -> tuple[TelegramOutbox, ...]:
    rows = await session.scalars(
        select(TelegramOutbox).where(
            TelegramOutbox.operation == _SOURCE_OPERATION,
            TelegramOutbox.state == OutboxState.CONFIRMED,
            TelegramOutbox.remote_message_id.is_not(None),
        )
    )
    return tuple(rows)


async def _existing_cleanup_keys(
    session: AsyncSession, sources: tuple[TelegramOutbox, ...]
) -> set[str]:
    keys = tuple(_cleanup_key(source) for source in sources)
    if not keys:
        return set()
    rows = await session.scalars(
        select(TelegramOutbox.business_key).where(TelegramOutbox.business_key.in_(keys))
    )
    return set(rows)


def _queue_delete(session: AsyncSession, source: TelegramOutbox, business_key: str) -> None:
    add_outbox(
        session,
        operation=_DELETE_OPERATION,
        aggregate_type="campaign",
        aggregate_id=source.aggregate_id,
        recipient_chat_id=source.recipient_chat_id,
        business_key=business_key,
        payload={"message_id": int(source.remote_message_id)},
    )
    add_audit(
        session,
        actor_user_id=None,
        action="campaign.legacy_commitment_notice_cleanup_queued",
        aggregate_type="campaign",
        aggregate_id=source.aggregate_id,
        reason="历史开奖随机承诺仅保留后台记录",
        details={"source_outbox_id": str(source.id)},
    )


def _cleanup_key(source: TelegramOutbox) -> str:
    return f"legacy_commitment_cleanup:{source.id}"
