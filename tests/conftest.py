import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.infrastructure.db import Base
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:lottery_test@127.0.0.1:55432/lottery_test"
)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    if "test" not in database_url.lower():
        raise RuntimeError("integration tests require a database URL containing 'test'")
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    yield factory
    table_names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    await engine.dispose()
