import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.infrastructure.db import Campaign

DOWN_REVISION = "2a4e91b6f703"
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:lottery_test@127.0.0.1:55432/lottery_test"
)


async def test_public_ref_downgrade_refuses_existing_campaign(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id

    result = await asyncio.to_thread(_downgrade_public_ref)

    assert result.returncode != 0
    assert "cannot downgrade while campaign public references exist" in (
        result.stdout + result.stderr
    )
    async with session_factory() as session:
        public_ref = await session.scalar(
            select(Campaign.public_ref).where(Campaign.id == campaign_id)
        )
        assert public_ref


def _downgrade_public_ref() -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    database_url = environment.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", DOWN_REVISION],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
