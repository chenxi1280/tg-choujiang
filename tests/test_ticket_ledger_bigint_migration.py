import asyncio
import os
import sys

import pytest
from sqlalchemy import select, text, update

from tests.test_v33_unlimited_runtime import current_rules
from tests.v30_helpers import seed_v3
from tg_lottery.infrastructure.db import TicketLedger


async def migrate(action, revision):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        action,
        revision,
        env=os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    return process.returncode, output.decode()


@pytest.mark.parametrize("delta", [2**31, -(2**31) - 1])
async def test_ledger_downgrade_refuses_large_values_without_changing_data(session_factory, delta):
    await seed_v3(session_factory, current_rules())
    async with session_factory() as session, session.begin():
        await session.execute(update(TicketLedger).values(delta=delta))
    code, output = await migrate("downgrade", "c33a1008bc02")
    assert code != 0 and "ticket_ledger.delta exceeds 32-bit range" in output
    async with session_factory() as session:
        assert await session.scalar(select(TicketLedger.delta)) == delta
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version")) == "c33a1026ir01"
        )


async def test_ledger_migration_round_trip_preserves_existing_rewards(session_factory):
    await seed_v3(session_factory, current_rules())
    try:
        code, output = await migrate("downgrade", "c33a1008bc02")
        assert code == 0, output
    finally:
        code, output = await migrate("upgrade", "head")
        assert code == 0, output
    async with session_factory() as session:
        assert await session.scalar(select(TicketLedger.delta)) == 1
