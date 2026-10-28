"""One-off end-to-end check; run only in the isolated Shanghai test executor."""

import asyncio
import json
import os
import sys

from tests.unknown_review_helpers import frozen_request, seed_unknown


async def call_module(module, *args, payload=b""):
    env = os.environ | {
        "BOT_TOKEN": "123456:" + "A" * 35,
        "BOT_ID": "123456",
        "APP_ENCRYPTION_KEY": "A" * 43 + "=",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout=25)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out.decode(), err.decode()


async def test_review_and_health_clis_preserve_unknown_and_fail_on_new_record(session_factory):
    row_id = await seed_unknown(session_factory)
    request = await frozen_request(session_factory, [row_id])
    raw = ("\n".join(json.dumps(row) for row in request.manifest) + "\n").encode()
    args = ("--manifest", "-", "--incident-ref", request.incident_ref, "--expected-count", "1")
    code, out, err = await call_module("tg_lottery.ops.unknown_review", *args, payload=raw)
    assert code == 0, err
    preview = json.loads(out)
    assert preview["applied"] is False and preview["count"] == 1
    code, out, err = await call_module(
        "tg_lottery.ops.unknown_review",
        *args,
        "--expected-fingerprint",
        preview["fingerprint"],
        payload=raw,
    )
    assert code == 0 and json.loads(out)["applied"] is True, err
    code, out, err = await call_module("tg_lottery.ops.delivery_health")
    assert code == 0 and "STATUS=DEGRADED_ACKNOWLEDGED" in out and "::warning" in out, err
    new_id = await seed_unknown(session_factory, "send_temporary_reminder")
    code, out, err = await call_module("tg_lottery.ops.delivery_health")
    assert code == 1 and f"UNKNOWN_UNREVIEWED outbox_id={new_id}" in out, err
