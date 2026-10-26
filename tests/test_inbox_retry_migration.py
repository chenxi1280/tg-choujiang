from datetime import timedelta

from sqlalchemy import select, text

from tests.test_inbox_ordering import _stored_update
from tests.test_ticket_ledger_bigint_migration import migrate
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import TelegramUpdate


async def test_downgrade_preserves_active_rate_limit(session_factory):
    row = _stored_update(100)
    row.state = UpdateState.FAILED
    row.next_attempt_at = utcnow() + timedelta(seconds=600)
    async with session_factory() as session, session.begin():
        session.add(row)
    code, output = await migrate("downgrade", "c33a1024bc03")
    assert code != 0 and "Inbox rate-limit deadline is pending" in output
    async with session_factory() as session:
        assert await session.scalar(select(TelegramUpdate.next_attempt_at)) == row.next_attempt_at
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version")) == "c33a1026ir01"
        )


async def test_upgrade_backfills_old_error_and_preserves_payload(session_factory):
    row = _stored_update(100)
    row.state = UpdateState.FAILED
    row.last_error = "old failure"
    async with session_factory() as session, session.begin():
        session.add(row)
    try:
        code, output = await migrate("downgrade", "c33a1024bc03")
        assert code == 0, output
    finally:
        code, output = await migrate("upgrade", "head")
        assert code == 0, output
    async with session_factory() as session:
        restored = await session.scalar(select(TelegramUpdate))
        assert restored.payload == row.payload
        assert restored.state is UpdateState.FAILED
        assert restored.last_failure == restored.last_error == "old failure"
        assert restored.next_attempt_at is None
