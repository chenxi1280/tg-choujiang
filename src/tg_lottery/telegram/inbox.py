import asyncio
import logging
from datetime import timedelta
from typing import Protocol

from aiogram.types import Update
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import TelegramUpdate

_PROCESSING_STALE_SECONDS = 300
_IDLE_WAIT_SECONDS = 0.5


class UpdateHandler(Protocol):
    async def handle(self, update: Update) -> None: ...


class InboxProcessor:
    def __init__(
        self,
        *,
        bot_id: int,
        handler: UpdateHandler,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._bot_id = bot_id
        self._handler = handler
        self._session_factory = session_factory
        self._logger = logging.getLogger(__name__)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            row = await self._claim_next()
            if row is None:
                await self._wait(stop)
                continue
            succeeded = await self._process(row)
            if not succeeded:
                await self._wait(stop)

    async def process_once(self) -> bool:
        row = await self._claim_next()
        if row is None:
            return False
        await self._process(row)
        return True

    async def _claim_next(self) -> TelegramUpdate | None:
        stale_before = utcnow() - timedelta(seconds=_PROCESSING_STALE_SECONDS)
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(TelegramUpdate)
                .where(
                    TelegramUpdate.bot_id == self._bot_id,
                    or_(
                        TelegramUpdate.state.in_([UpdateState.RECEIVED, UpdateState.FAILED]),
                        (
                            (TelegramUpdate.state == UpdateState.PROCESSING)
                            & (TelegramUpdate.updated_at < stale_before)
                        ),
                    ),
                )
                .order_by(TelegramUpdate.update_id)
                .limit(1)
                .with_for_update()
            )
            if row is None:
                return None
            row.state = UpdateState.PROCESSING
            row.attempts += 1
            row.last_error = None
            await session.flush()
            session.expunge(row)
            return row

    async def _process(self, row: TelegramUpdate) -> bool:
        if row.payload is None:
            await self._mark_failed(row.id, "update payload was pruned before processing")
            return False
        try:
            update = Update.model_validate(row.payload)
            await self._handler.handle(update)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.exception(
                "durable update processing failed",
                extra={"bot_id": row.bot_id, "update_id": row.update_id},
            )
            await self._mark_failed(row.id, f"{type(exc).__name__}: {exc}")
            return False
        await self._mark_processed(row.id)
        return True

    async def _mark_processed(self, row_id: int) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(TelegramUpdate, row_id, with_for_update=True)
            if row is None:
                raise RuntimeError("claimed update disappeared")
            row.state = UpdateState.PROCESSED
            row.processed_at = utcnow()
            row.last_error = None

    async def _mark_failed(self, row_id: int, error: str) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(TelegramUpdate, row_id, with_for_update=True)
            if row is None:
                raise RuntimeError("claimed update disappeared")
            row.state = UpdateState.FAILED
            row.last_error = error[:4000]

    async def _wait(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=_IDLE_WAIT_SECONDS)
        except TimeoutError:
            return
