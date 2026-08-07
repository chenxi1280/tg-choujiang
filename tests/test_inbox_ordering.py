from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import Update
from sqlalchemy import select

from tg_lottery.application.records import utcnow
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import TelegramUpdate
from tg_lottery.telegram.inbox import InboxProcessor


class FlakyHandler:
    def __init__(self) -> None:
        self.calls = 0
        self.update_ids: list[int | None] = []

    async def handle(self, update: Update) -> None:
        self.calls += 1
        self.update_ids.append(current_update_id())
        if self.calls == 1:
            raise RuntimeError("planned failure")


async def test_inbox_failure_remains_replayable(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add(_stored_update(20))
    handler = FlakyHandler()
    processor = _processor(handler, session_factory)
    assert await processor.process_once() is True
    async with session_factory() as session:
        row = await session.scalar(select(TelegramUpdate))
        assert row.state is UpdateState.FAILED
    assert await processor.process_once() is False
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(TelegramUpdate).with_for_update())
        row.updated_at = utcnow() - timedelta(seconds=6)
    assert await processor.process_once() is True
    async with session_factory() as session:
        row = await session.scalar(select(TelegramUpdate))
        assert row.state is UpdateState.PROCESSED
        assert row.attempts == 2
        assert handler.update_ids == [20, 20]


async def test_inbox_blocks_later_update_while_earlier_is_processing(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add_all([_stored_update(update_id) for update_id in (40, 41)])
    first = _processor(FlakyHandler(), session_factory)
    second = _processor(FlakyHandler(), session_factory)
    first_row = await first._claim_next()
    assert first_row is not None and first_row.update_id == 40
    assert await second._claim_next() is None
    await first._mark_processed(first_row.id, first_row.attempts)
    second_row = await second._claim_next()
    assert second_row is not None and second_row.update_id == 41


async def test_stale_inbox_owner_cannot_overwrite_reclaimed_result(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add(_stored_update(42))
    first = _processor(FlakyHandler(), session_factory)
    second = _processor(FlakyHandler(), session_factory)
    stale_claim = await first._claim_next()
    assert stale_claim is not None
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramUpdate, stale_claim.id)
        row.updated_at = utcnow() - timedelta(seconds=301)
    fresh_claim = await second._claim_next()
    assert fresh_claim is not None
    await second._mark_processed(fresh_claim.id, fresh_claim.attempts)
    with pytest.raises(RuntimeError, match="ownership lost"):
        await first._mark_failed(stale_claim.id, stale_claim.attempts, "late failure")


def _processor(handler, session_factory) -> InboxProcessor:
    return InboxProcessor(bot_id=123, handler=handler, session_factory=session_factory)


def _stored_update(update_id: int) -> TelegramUpdate:
    update = _update(update_id)
    return TelegramUpdate(
        bot_id=123,
        update_id=update_id,
        update_type="message",
        event_at=datetime.now(UTC),
        payload=update.model_dump(mode="json", exclude_none=True),
        payload_hash=str(update_id).zfill(64),
        state=UpdateState.RECEIVED,
        attempts=0,
    )


def _update(update_id: int) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 201, "type": "private"},
                "from": {"id": 201, "is_bot": False, "first_name": "Tester"},
                "text": "/start",
            },
        }
    )
