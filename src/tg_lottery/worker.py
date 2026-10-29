import asyncio
from functools import partial

from tg_lottery.application.featured_channel import FeaturedChannelService
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
        featured = await _featured_service(container, bot_username)
        handlers = JobHandlers(
            bot=container.bot,
            session_factory=container.session_factory,
            participants=container.services.participants,
            invitations=container.services.invitations,
            settlement=container.services.settlement,
            drawing=container.services.drawing,
            message_stats=container.services.message_stats,
            qualification_requests_per_second=settings.qualification_requests_per_second,
            featured_channel=featured,
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


async def _featured_service(container, bot_username):
    settings = container.settings
    if not settings.featured_push_enabled:
        return None
    service = FeaturedChannelService(
        container.session_factory,
        chat_id=settings.featured_push_chat_id,
        bot_username=bot_username,
        digest_hour=settings.featured_digest_hour,
        verify_target=partial(_verify_featured_target, container),
    )
    await service.ensure_job(utcnow())
    return service


async def _verify_featured_target(container):
    settings = container.settings
    chat = await container.bot.get_chat(settings.featured_push_chat_id)
    member = await container.bot.get_chat_member(chat.id, settings.bot_id)
    if str(chat.type) != "channel" or member.status != "administrator":
        raise ValueError("featured push target must be a channel administered by this bot")
    if not member.can_post_messages or not member.can_edit_messages:
        raise ValueError("featured channel requires post and edit message permissions")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
