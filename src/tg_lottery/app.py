import asyncio

from tg_lottery.bootstrap import build_container, build_update_handler
from tg_lottery.config import get_settings
from tg_lottery.runtime import configure_logging, owner_id, stop_event, validate_bot
from tg_lottery.telegram.inbox import InboxProcessor
from tg_lottery.telegram.polling import TelegramPollingAdapter


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    stop = stop_event()
    try:
        bot_username = await validate_bot(container.bot, settings.bot_id)
        handler = build_update_handler(container, bot_username)
        polling = TelegramPollingAdapter(
            bot=container.bot,
            bot_id=settings.bot_id,
            owner_id=owner_id("polling"),
            session_factory=container.session_factory,
            timeout_seconds=settings.poll_timeout_seconds,
            lease_seconds=settings.poll_lease_seconds,
        )
        inbox = InboxProcessor(
            bot=container.bot,
            bot_id=settings.bot_id,
            handler=handler,
            session_factory=container.session_factory,
        )
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(polling.run_forever(stop))
            tasks.create_task(inbox.run_forever(stop))
    finally:
        await container.bot.session.close()
        await container.engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
