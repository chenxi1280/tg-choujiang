import asyncio
import sys

from tests.v30_helpers import rules_for, seed_v3


async def test_destructive_downgrade_refuses_existing_v3_campaign(session_factory):
    await seed_v3(session_factory, rules_for("message"))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "downgrade",
        "b7e2a1d9c403",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
    assert process.returncode != 0
    assert b"V3.0 campaign facts exist" in output
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tg_lottery.rule_capabilities",
        "--check",
        '["legacy"]',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
    assert process.returncode != 0 and b"incompatible rule capabilities" in output
