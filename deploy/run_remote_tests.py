from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

PASSWORD_FILE = Path("/run/secrets/postgres_password")
DATABASE_NAME = "tg_lottery_test"
TEST_TIMEOUT_SECONDS = 60
SHARD_COUNT = 8


def _environment() -> dict[str, str]:
    instance_id = os.environ["TEST_DATABASE_INSTANCE_ID"]
    password = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("test database password file is empty")
    database_url = (
        f"postgresql+asyncpg://{DATABASE_NAME}:{quote(password, safe='')}"
        f"@127.0.0.1:5432/{DATABASE_NAME}"
    )
    return os.environ | {
        "TEST_DATABASE_INSTANCE_ID": instance_id,
        "TEST_DATABASE_URL": database_url,
        "DATABASE_URL": database_url,
    }


def _run(command: list[str], environment: dict[str, str]) -> None:
    subprocess.run(command, env=environment, check=True, timeout=TEST_TIMEOUT_SECONDS)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode not in {"all", "migrate", "pytest"}:
        raise ValueError(f"unknown test mode: {mode}")
    environment = _environment()
    if mode in {"all", "migrate"}:
        _run(["alembic", "upgrade", "head"], environment)
        _run(["alembic", "check"], environment)
    if mode == "all":
        for index in range(SHARD_COUNT):
            _run(
                [
                    sys.executable,
                    "scripts/run_pytest_shard.py",
                    "--index",
                    str(index),
                    "--count",
                    str(SHARD_COUNT),
                ],
                environment,
            )
    if mode == "pytest":
        _run([sys.executable, "-m", "pytest", *sys.argv[2:]], environment)


if __name__ == "__main__":
    main()
