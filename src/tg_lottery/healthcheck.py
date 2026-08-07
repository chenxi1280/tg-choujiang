import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import select, text

from tg_lottery.application.records import utcnow
from tg_lottery.config import get_settings
from tg_lottery.infrastructure.db import PollingOffset, WorkerHeartbeat
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory

_POLLING_MAX_AGE_SECONDS = 180
_WORKER_MAX_AGE_SECONDS = 180


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
        if integrity_status == "integrity_review_required":
            raise RuntimeError("polling update continuity requires review")
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
