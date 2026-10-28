import asyncio

from tg_lottery.application.legacy_commitment_cleanup import (
    queue_legacy_commitment_notice_cleanup,
)
from tg_lottery.application.records import utcnow
from tg_lottery.bootstrap import build_container
from tg_lottery.config import get_settings
from tg_lottery.jobs.handlers import JobHandlers
from tg_lottery.jobs.maintenance import ensure_maintenance_jobs
from tg_lottery.jobs.worker import JobWorker
from tg_lottery.runtime import configure_logging, owner_id, stop_event, validate_bot
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.outbox import OutboxWorker
from tg_lottery.telegram.single_request_session import SingleRequestSession


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings, bot_session=SingleRequestSession())
    stop = stop_event()
    job_owner = owner_id("jobs")
    try:
        bot_username = await validate_bot(container.bot, settings.bot_id)
        await ensure_maintenance_jobs(container.session_factory, utcnow())
        await queue_legacy_commitment_notice_cleanup(container.session_factory)
        handlers = JobHandlers(
            bot=container.bot,
            session_factory=container.session_factory,
            participants=container.services.participants,
            invitations=container.services.invitations,
            settlement=container.services.settlement,
            drawing=container.services.drawing,
            message_stats=container.services.message_stats,
            qualification_requests_per_second=settings.qualification_requests_per_second,
        )
        job_worker = JobWorker(
            owner_id=job_owner,
            worker_name="scheduled-jobs",
            session_factory=container.session_factory,
            handlers=handlers,
            poll_seconds=settings.worker_poll_seconds,
        )
        cipher = SecretCipher(settings.app_encryption_key)
        outbox = OutboxWorker(
            bot_id=settings.bot_id,
            owner_id=owner_id("outbox"),
            session_factory=container.session_factory,
            gateway=TelegramGateway(container.bot, cipher, bot_username=bot_username),
            cipher=cipher,
        )
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(job_worker.run_forever(stop))
            tasks.create_task(outbox.run_forever(stop))
    finally:
        await container.bot.session.close()
        await container.engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
