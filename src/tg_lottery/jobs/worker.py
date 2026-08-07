import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import JobState
from tg_lottery.infrastructure.db import ScheduledJob, WorkerHeartbeat
from tg_lottery.infrastructure.heartbeat import run_with_heartbeat
from tg_lottery.jobs.errors import JobBlocked, JobRetry
from tg_lottery.jobs.handlers import JobHandlers

_CLAIM_LEASE_SECONDS = 120
_LEASE_RENEW_INTERVAL_SECONDS = 30
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
        lease_seconds: int = _CLAIM_LEASE_SECONDS,
        renew_interval_seconds: float = _LEASE_RENEW_INTERVAL_SECONDS,
    ) -> None:
        if lease_seconds <= 0 or not 0 < renew_interval_seconds < lease_seconds:
            raise ValueError("job lease renewal interval must be shorter than the lease")
        self._owner_id = owner_id
        self._worker_name = worker_name
        self._session_factory = session_factory
        self._handlers = handlers
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._renew_interval_seconds = renew_interval_seconds
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
            await self._handle_with_renewal(job)
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
            self._logger.info(
                "scheduled job succeeded",
                extra={"job_id": str(job.id), "job_type": job.job_type},
            )

    async def _handle_with_renewal(self, job: ScheduledJob) -> None:
        await run_with_heartbeat(
            self._handlers.handle(job),
            lambda: self._renew_claim(job.id),
            self._renew_interval_seconds,
        )

    async def _renew_claim(self, job_id: object) -> None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            job = await self._locked_owned_job(session, job_id)
            job.lease_until = now + timedelta(seconds=self._lease_seconds)
            await session.execute(self._heartbeat_statement(now))

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
            job.lease_until = now + timedelta(seconds=self._lease_seconds)
            job.attempts += 1
            await session.flush()
            session.expunge(job)
            return job

    async def _retry(self, job_id: object, error: str, delay_seconds: int) -> None:
        async with self._session_factory() as session, session.begin():
            job = await self._locked_owned_job(session, job_id)
            job.state = JobState.RETRY_SCHEDULED
            job.run_at = utcnow() + timedelta(seconds=delay_seconds)
            job.last_error = error[:4000]
            job.lease_owner = None
            job.lease_until = None

    async def _finish(self, job_id: object, state: JobState, error: str | None) -> None:
        async with self._session_factory() as session, session.begin():
            job = await self._locked_owned_job(session, job_id)
            job.state = state
            job.last_error = error[:4000] if error else None
            job.lease_owner = None
            job.lease_until = None

    async def _locked_owned_job(self, session: AsyncSession, job_id: object) -> ScheduledJob:
        job = await session.get(ScheduledJob, job_id, with_for_update=True)
        if job is None:
            raise RuntimeError("claimed job disappeared")
        if job.state is not JobState.CLAIMED or job.lease_owner != self._owner_id:
            raise RuntimeError(
                f"job claim ownership lost: job={job.id} owner={self._owner_id} "
                f"current_owner={job.lease_owner} state={job.state}"
            )
        return job

    async def _heartbeat(self) -> None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            await session.execute(self._heartbeat_statement(now))

    def _heartbeat_statement(self, now: datetime):
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
        return statement

    async def _wait(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
        except TimeoutError:
            return
