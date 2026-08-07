import pytest

from tg_lottery.application.records import utcnow
from tg_lottery.healthcheck import _check_role
from tg_lottery.infrastructure.db import PollingOffset


async def test_app_health_rejects_any_non_ok_polling_continuity(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            PollingOffset(
                bot_id=123,
                last_durable_update_id=10,
                last_poll_succeeded_at=utcnow(),
                updated_at=utcnow(),
                integrity_status="gap_observed",
            )
        )
    async with session_factory() as session:
        with pytest.raises(RuntimeError, match="continuity is not ok"):
            await _check_role(session, "app", 123)
