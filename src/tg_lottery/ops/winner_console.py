"""Authenticated server operations entry; never registers commands on the public bot."""

import argparse
import asyncio
import os
import sys
from functools import partial

from tg_lottery.application.queries import QueryService
from tg_lottery.application.winner_designations import WinnerDesignationService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.ops_winner_designations import OpsWinnerDesignationHandler


async def execute(*, actor_user_id: int, command: str) -> str:
    allowed = frozenset(
        int(value.strip()) for value in os.environ["OPS_ADMIN_IDS"].split(",") if value.strip()
    )
    if actor_user_id not in allowed:
        raise UserFacingError("当前操作人不在 OPS_ADMIN_IDS 运维白名单中")
    engine = create_engine(os.environ["DATABASE_URL"])
    sessions = create_session_factory(engine)
    try:
        handler = OpsWinnerDesignationHandler(
            service=WinnerDesignationService(partial(UnitOfWork, sessions)),
            queries=QueryService(sessions),
            allowed_user_ids=allowed,
            source_bot_id=None,
        )
        return await handler.execute(command, actor_user_id=actor_user_id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="仅供服务器管理员使用的后台中奖指定控制台")
    parser.add_argument("--actor", type=int, required=True, help="白名单中的操作人 Telegram 身份")
    parser.add_argument(
        "command", help="/campaigns、/winner_list、/winner_set 或 /winner_del 完整命令"
    )
    args = parser.parse_args()
    try:
        print(asyncio.run(execute(actor_user_id=args.actor, command=args.command)))
    except UserFacingError as exc:
        print(exc.public_message, file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
