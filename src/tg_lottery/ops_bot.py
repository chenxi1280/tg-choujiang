import asyncio
from functools import partial

from aiogram import Bot, Dispatcher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.queries import QueryService
from tg_lottery.application.winner_designations import WinnerDesignationService
from tg_lottery.infrastructure.db import PollingOffset
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.ops_bot_config import OpsBotSettings, get_ops_bot_settings
from tg_lottery.runtime import configure_logging, validate_bot
from tg_lottery.telegram.ops_winner_designations import OpsWinnerDesignationHandler


async def run(settings: OpsBotSettings | None = None) -> None:
    resolved = settings or get_ops_bot_settings()
    configure_logging(resolved.log_level)
    engine = create_engine(resolved.database_url)
    session_factory = create_session_factory(engine)
    bot = Bot(resolved.ops_bot_token.get_secret_value())
    try:
        await _require_isolated_bot_id(session_factory, resolved.ops_bot_id)
        await validate_bot(bot, resolved.ops_bot_id)
        handler = OpsWinnerDesignationHandler(
            service=WinnerDesignationService(partial(UnitOfWork, session_factory)),
            queries=QueryService(session_factory),
            allowed_user_ids=resolved.ops_admin_ids,
            source_bot_id=resolved.ops_bot_id,
        )
        dispatcher = Dispatcher()
        dispatcher.message.register(handler.handle)
        await dispatcher.start_polling(bot, allowed_updates=["message"])
    finally:
        await bot.session.close()
        await engine.dispose()


async def _require_isolated_bot_id(
    session_factory: async_sessionmaker[AsyncSession], bot_id: int
) -> None:
    async with session_factory() as session:
        online_bot_id = await session.scalar(
            select(PollingOffset.bot_id).where(PollingOffset.bot_id == bot_id)
        )
    if online_bot_id is not None:
        raise RuntimeError("OPS_BOT_TOKEN must not reuse an online bot identity")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
