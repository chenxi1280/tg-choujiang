import asyncio
import logging
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import JobState
from tg_lottery.infrastructure.db import ScheduledJob, WorkerHeartbeat
from tg_lottery.jobs.errors import JobBlocked, JobRetry
from tg_lottery.jobs.handlers import JobHandlers

_CLAIM_LEASE_SECONDS = 120
_UNEXPECTED_RETRY_SECONDS = 30


class JobWorker:
    def __init__(
        self,
        *,
        owner_id: str,
        worker_name: str,
        session_factory: async_sessionmaker[AsyncSession],
        handlers: JobHandlers,
        poll_seconds: float,
    ) -> None:
        self._owner_id = owner_id
        self._worker_name = worker_name
        self._session_factory = session_factory
        self._handlers = handlers
        self._poll_seconds = poll_seconds
        self._logger = logging.getLogger(__name__)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._heartbeat()
            job = await self._claim_next()
            if job is None:
                await self._wait(stop)
                continue
            await self._execute(job)

    async def process_once(self) -> bool:
        await self._heartbeat()
        job = await self._claim_next()
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _execute(self, job: ScheduledJob) -> None:
        try:
            await self._handlers.handle(job)
        except JobRetry as exc:
            await self._retry(job.id, str(exc), exc.delay_seconds)
        except JobBlocked as exc:
            await self._finish(job.id, JobState.BLOCKED, str(exc))
        except ValueError as exc:
            await self._finish(job.id, JobState.FAILED_TERMINAL, str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.exception(
                "scheduled job failed unexpectedly",
                extra={"job_id": str(job.id), "job_type": job.job_type},
            )
            await self._retry(job.id, f"{type(exc).__name__}: {exc}", _UNEXPECTED_RETRY_SECONDS)
        else:
            await self._finish(job.id, JobState.SUCCEEDED, None)

    async def _claim_next(self) -> ScheduledJob | None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            job = await session.scalar(
                select(ScheduledJob)
                .where(
                    ScheduledJob.run_at <= now,
                    or_(
                        ScheduledJob.state.in_([JobState.PENDING, JobState.RETRY_SCHEDULED]),
                        (
                            (ScheduledJob.state == JobState.CLAIMED)
                            & (ScheduledJob.lease_until < now)
                        ),
                    ),
                )
                .order_by(ScheduledJob.run_at, ScheduledJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            job.state = JobState.CLAIMED
            job.lease_owner = self._owner_id
            job.lease_until = now + timedelta(seconds=_CLAIM_LEASE_SECONDS)
            job.attempts += 1
            await session.flush()
            session.expunge(job)
            return job

    async def _retry(self, job_id: object, error: str, delay_seconds: int) -> None:
        async with self._session_factory() as session, session.begin():
            job = await session.get(ScheduledJob, job_id, with_for_update=True)
            if job is None:
                raise RuntimeError("claimed job disappeared")
            job.state = JobState.RETRY_SCHEDULED
            job.run_at = utcnow() + timedelta(seconds=delay_seconds)
            job.last_error = error[:4000]
            job.lease_owner = None
            job.lease_until = None

    async def _finish(self, job_id: object, state: JobState, error: str | None) -> None:
        async with self._session_factory() as session, session.begin():
            job = await session.get(ScheduledJob, job_id, with_for_update=True)
            if job is None:
                raise RuntimeError("claimed job disappeared")
            job.state = state
            job.last_error = error[:4000] if error else None
            job.lease_owner = None
            job.lease_until = None

    async def _heartbeat(self) -> None:
        now = utcnow()
        statement = insert(WorkerHeartbeat).values(
            worker_name=self._worker_name,
            owner_id=self._owner_id,
            last_seen_at=now,
            details={},
        )
        statement = statement.on_conflict_do_update(
            index_elements=["worker_name"],
            set_={"owner_id": self._owner_id, "last_seen_at": now},
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(statement)

    async def _wait(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
        except TimeoutError:
            return
