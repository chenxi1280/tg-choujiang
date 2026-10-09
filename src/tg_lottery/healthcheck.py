import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import select, text

from tg_lottery.application.records import utcnow
from tg_lottery.config import get_settings
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import PollingOffset, TelegramUpdate, WorkerHeartbeat
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory

_POLLING_MAX_AGE_SECONDS = 180
_WORKER_MAX_AGE_SECONDS = 180
_INBOX_MAX_AGE_SECONDS = 120


async def check(role: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
            await _check_role(session, role, settings.bot_id)
    finally:
        await engine.dispose()


async def _check_role(session: object, role: str, bot_id: int) -> None:
    if role == "app":
        result = await session.execute(
            select(
                PollingOffset.last_poll_succeeded_at,
                PollingOffset.integrity_status,
            ).where(PollingOffset.bot_id == bot_id)
        )
        row = result.one_or_none()
        if row is None:
            raise RuntimeError("polling heartbeat is missing")
        last_seen, integrity_status = row
        _require_fresh(last_seen, _POLLING_MAX_AGE_SECONDS, "polling")
        if integrity_status != "ok":
            raise RuntimeError(f"polling update continuity is not ok: {integrity_status}")
        await _check_inbox(session, bot_id)
        return
    if role == "worker":
        last_seen = await session.scalar(
            select(WorkerHeartbeat.last_seen_at).where(
                WorkerHeartbeat.worker_name == "scheduled-jobs"
            )
        )
        _require_fresh(last_seen, _WORKER_MAX_AGE_SECONDS, "worker")
        return
    raise ValueError(f"unknown healthcheck role: {role}")


async def _check_inbox(session, bot_id: int) -> None:
    oldest = await session.scalar(
        select(TelegramUpdate)
        .where(TelegramUpdate.bot_id == bot_id, TelegramUpdate.state != UpdateState.PROCESSED)
        .order_by(TelegramUpdate.update_id)
        .limit(1)
    )
    if oldest is not None and oldest.created_at < utcnow() - timedelta(
        seconds=_INBOX_MAX_AGE_SECONDS
    ):
        raise RuntimeError(
            f"update processing queue is blocked: update={oldest.update_id} "
            f"state={oldest.state.value} received_at={oldest.created_at}"
        )


def _require_fresh(last_seen: object, max_age_seconds: int, label: str) -> None:
    if last_seen is None:
        raise RuntimeError(f"{label} heartbeat is missing")
    if last_seen < utcnow() - timedelta(seconds=max_age_seconds):
        raise RuntimeError(f"{label} heartbeat is stale: {last_seen}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["app", "worker"], required=True)
    arguments = parser.parse_args()
    asyncio.run(check(arguments.role))


if __name__ == "__main__":
    main()
