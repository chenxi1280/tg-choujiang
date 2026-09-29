from __future__ import annotations

import asyncio
import ssl
from pathlib import Path

import asyncpg
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

EXPECTED_DATABASE = "tg_lottery_test"
EXPECTED_ROLE = "tg_lottery_test"
CONFIG_FILE = Path(__file__).resolve().parents[1] / ".env.test"


async def main() -> None:
    config = dotenv_values(CONFIG_FILE)
    url = make_url(config["TEST_DATABASE_URL"])
    if url.query.get("ssl") != "verify-full":
        raise RuntimeError("TEST_DATABASE_URL must require verified TLS")
    context = ssl.create_default_context(cafile=config["PGSSLROOTCERT"])
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
        ssl=context,
    )
    try:
        actual = await connection.fetchrow(
            "SELECT current_database() AS database, current_user AS role, "
            "current_setting('tg_lottery.test_database_instance', true) AS instance, "
            "ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
        )
    finally:
        await connection.close()
    expected = (EXPECTED_DATABASE, EXPECTED_ROLE, config["TEST_DATABASE_INSTANCE_ID"])
    if tuple(actual[:3]) != expected or actual["ssl"] is not True:
        raise RuntimeError(f"test database identity or TLS mismatch: {tuple(actual)}")
    print("test database verified:", *actual)


if __name__ == "__main__":
    asyncio.run(main())
