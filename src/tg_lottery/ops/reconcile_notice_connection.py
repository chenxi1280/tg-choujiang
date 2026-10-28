"""Reconcile one verified pre-connection failure for a notice or its deletion."""

import argparse
import asyncio
import json
from uuid import UUID

from tg_lottery.config import get_settings
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.ops.reconcile_invite_connection import RepairOptions, reconcile_notice_connection


def _parse_options() -> RepairOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox-id", type=UUID, required=True)
    parser.add_argument("--runtime-ref", required=True)
    parser.add_argument("--repair-ref", required=True)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--retry-delete", action="store_true")
    parser.add_argument("--unredirected-evidence-ref", default="")
    return RepairOptions(**vars(parser.parse_args()))


async def _run(options: RepairOptions) -> None:
    engine = create_engine(get_settings().database_url)
    try:
        report = await reconcile_notice_connection(create_session_factory(engine), options)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run(_parse_options()))
