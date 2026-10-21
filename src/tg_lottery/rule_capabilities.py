"""The executable image declares and checks its ability to fulfill stored rules."""

import asyncio
import json
import os
import sys

from sqlalchemy import text

from tg_lottery.infrastructure.db.session import create_engine

SUPPORTED_RULE_VERSIONS = frozenset(
    {"legacy", "v3.0", "platform:official-v1", "platform:official-v2", "bonus:unlimited-v1"}
)


def require_capabilities(required, supported):
    missing = set(required).difference(supported)
    if missing:
        raise RuntimeError(f"incompatible rule capabilities: {sorted(missing)}")


async def check_database(supported):
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            versions = await connection.scalars(
                text(
                    "SELECT DISTINCT COALESCE(rules->>'participation_rules_version', 'legacy') "
                    "FROM campaigns"
                    " UNION SELECT 'platform:' || (rules->'platform_policy'->>'version') "
                    "FROM campaigns WHERE rules->'platform_policy'->>'version' IS NOT NULL"
                    " UNION SELECT 'bonus:' || (rules->>'bonus_policy_version') "
                    "FROM campaigns WHERE rules->>'bonus_policy_version' IS NOT NULL"
                )
            )
            require_capabilities(versions, supported)
    finally:
        await engine.dispose()
    print("stored campaign rules are supported")


def main():
    if sys.argv[1:] == ["--describe"]:
        print(json.dumps(sorted(SUPPORTED_RULE_VERSIONS)))
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        asyncio.run(check_database(json.loads(sys.argv[2])))
        return
    raise ValueError("expected --describe or --check <supported versions JSON>")


if __name__ == "__main__":
    main()
