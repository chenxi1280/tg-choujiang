import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tg_lottery.infrastructure.db import Base
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory

TEST_DATABASE_NAME = "tg_lottery_test"
TEST_DATABASE_ROLE = "tg_lottery_test"


async def _verify_test_database(engine: AsyncEngine, expected_instance: str) -> None:
    async with engine.connect() as connection:
        identity = (
            await connection.execute(
                text(
                    "SELECT current_database(), current_user, "
                    "current_setting('tg_lottery.test_database_instance', true)"
                )
            )
        ).one()
    expected = (TEST_DATABASE_NAME, TEST_DATABASE_ROLE, expected_instance)
    if tuple(identity) != expected:
        raise RuntimeError(
            f"test database identity mismatch: expected {expected}, got {tuple(identity)}"
        )


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.environ["TEST_DATABASE_URL"]
    instance_id = os.environ["TEST_DATABASE_INSTANCE_ID"]
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    try:
        await _verify_test_database(engine, instance_id)
        yield factory
        table_names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()
