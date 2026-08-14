import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.infrastructure.db import DailyProgress

DOWN_REVISION = "b91e4d7a6c20"
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:lottery_test@127.0.0.1:55432/lottery_test"
)


async def test_upgrade_backfills_existing_progress_for_main_group(session_factory) -> None:
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session)
        progress = DailyProgress(
            campaign_id=campaign.id,
            participant_id=participant.id,
            local_date=date.today(),
            valid_message_count=7,
            valid_message_counts={str(group.chat_id): 7},
            tickets_awarded=1,
        )
        session.add(progress)
        await session.flush()
        progress_id = progress.id
        chat_id = group.chat_id

    await asyncio.to_thread(_run_alembic, "downgrade", DOWN_REVISION)
    await asyncio.to_thread(_run_alembic, "upgrade", "head")

    async with session_factory() as session:
        restored = await session.scalar(
            select(DailyProgress).where(DailyProgress.id == progress_id)
        )
    assert restored is not None
    assert restored.valid_message_count == 7
    assert restored.valid_message_counts == {str(chat_id): 7}


def _run_alembic(action: str, revision: str) -> None:
    environment = os.environ.copy()
    database_url = environment.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", action, revision],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
