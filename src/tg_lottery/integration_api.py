"""Dedicated read-only listener. Never starts a Telegram polling consumer."""

import asyncio
import hmac
import re

from aiohttp import web
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tg_lottery.application.integration_snapshot import SnapshotRequest, campaign_snapshot
from tg_lottery.config import get_settings
from tg_lottery.infrastructure.db.session import create_session_factory
from tg_lottery.integration_config import IntegrationSettings
from tg_lottery.runtime import stop_event


def create_app(factory, settings, *, bot_id, snapshot=campaign_snapshot):
    read_slots = asyncio.Semaphore(2)

    async def handler(request):
        expected = "Bearer " + settings.token.get_secret_value()
        if not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), expected.encode()
        ):
            raise web.HTTPUnauthorized()
        public_ref = request.match_info["public_ref"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", public_ref):
            raise web.HTTPBadRequest(text="invalid campaign reference")
        try:
            scope = SnapshotRequest.model_validate_json(await request.read())
        except ValidationError as error:
            raise web.HTTPBadRequest(text="invalid snapshot scope") from error
        async with asyncio.timeout(10), read_slots, factory() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            value = await snapshot(
                session, public_ref, scope, creator_ids=settings.creator_ids, bot_id=bot_id
            )
        if value is None:
            raise web.HTTPNotFound()
        return web.json_response(value, headers={"Cache-Control": "no-store"})

    app = web.Application(client_max_size=16 * 1024)
    app.router.add_post("/v1/campaigns/{public_ref}/snapshot", handler)
    return app


async def run():
    integration = IntegrationSettings()
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
    runner = web.AppRunner(
        create_app(create_session_factory(engine), integration, bot_id=settings.bot_id),
        access_log=None,
    )
    try:
        await runner.setup()
        await web.TCPSite(runner, integration.host, integration.port).start()
        await stop_event().wait()
    finally:
        await runner.cleanup()
        await engine.dispose()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
