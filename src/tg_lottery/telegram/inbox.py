import asyncio
import logging
from datetime import timedelta
from time import perf_counter
from typing import Protocol

from aiogram import Bot
from aiogram.types import Update
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from tg_lottery.application.records import utcnow
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import TelegramUpdate

_PROCESSING_STALE_SECONDS = 300
_FAILED_RETRY_SECONDS = 5
_IDLE_WAIT_SECONDS = 0.5


class UpdateHandler(Protocol):
    async def handle(self, update: Update) -> None: ...


class InboxProcessor:
    def __init__(
        self,
        *,
        bot: Bot,
        bot_id: int,
        handler: UpdateHandler,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._bot = bot
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
        now = utcnow()
        stale_before = now - timedelta(seconds=_PROCESSING_STALE_SECONDS)
        retry_before = now - timedelta(seconds=_FAILED_RETRY_SECONDS)
        earlier = aliased(TelegramUpdate)
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(TelegramUpdate)
                .where(
                    TelegramUpdate.bot_id == self._bot_id,
                    or_(
                        TelegramUpdate.state == UpdateState.RECEIVED,
                        (
                            (TelegramUpdate.state == UpdateState.FAILED)
                            & (TelegramUpdate.updated_at <= retry_before)
                        ),
                        (
                            (TelegramUpdate.state == UpdateState.PROCESSING)
                            & (TelegramUpdate.updated_at < stale_before)
                        ),
                    ),
                    ~exists().where(
                        earlier.bot_id == TelegramUpdate.bot_id,
                        earlier.update_id < TelegramUpdate.update_id,
                        earlier.state != UpdateState.PROCESSED,
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
        started = perf_counter()
        queue_wait_ms = (
            (utcnow() - row.created_at).total_seconds() * 1000 if row.created_at else None
        )
        if row.payload is None:
            await self._mark_failed(
                row.id, row.attempts, "update payload was pruned before processing"
            )
            return False
        try:
            update = Update.model_validate(row.payload, context={"bot": self._bot})
            with bind_update_context(row.bot_id, row.update_id):
                await self._handler.handle(update)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.exception(
                "durable update processing failed",
                extra={
                    "bot_id": row.bot_id,
                    "update_id": row.update_id,
                    "elapsed_ms": round((perf_counter() - started) * 1000),
                    "queue_wait_ms": queue_wait_ms,
                },
            )
            await self._mark_failed(row.id, row.attempts, f"{type(exc).__name__}: {exc}")
            return False
        await self._mark_processed(row.id, row.attempts)
        self._logger.info(
            "durable update processed",
            extra={
                "bot_id": row.bot_id,
                "update_id": row.update_id,
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "queue_wait_ms": queue_wait_ms,
            },
        )
        return True

    async def _mark_processed(self, row_id: int, claimed_attempt: int) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_claim(session, row_id, claimed_attempt)
            row.state = UpdateState.PROCESSED
            row.processed_at = utcnow()
            row.last_error = None

    async def _mark_failed(self, row_id: int, claimed_attempt: int, error: str) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_claim(session, row_id, claimed_attempt)
            row.state = UpdateState.FAILED
            row.last_error = error[:4000]

    async def _locked_claim(
        self, session: AsyncSession, row_id: int, claimed_attempt: int
    ) -> TelegramUpdate:
        row = await session.get(TelegramUpdate, row_id, with_for_update=True)
        if row is None:
            raise RuntimeError("claimed update disappeared")
        if row.state is not UpdateState.PROCESSING or row.attempts != claimed_attempt:
            raise RuntimeError(
                f"update claim ownership lost: update={row.update_id} "
                f"attempt={claimed_attempt} current_attempt={row.attempts} state={row.state}"
            )
        return row

    async def _wait(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=_IDLE_WAIT_SECONDS)
        except TimeoutError:
            return
