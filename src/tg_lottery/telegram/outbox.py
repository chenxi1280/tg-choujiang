import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.bot_relationships import record_blocked_delivery
from tg_lottery.application.operational_blocks import (
    record_outbox_target_block,
    resolve_outbox_target_block,
)
from tg_lottery.application.outbox_claims import (
    lock_presend_claim,
    start_claimed_gateway,
)
from tg_lottery.application.records import utcnow
from tg_lottery.application.retention_policy import apply_retention
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import (
    DeliveryAttempt,
    TelegramOutbox,
)
from tg_lottery.infrastructure.heartbeat import run_with_heartbeat
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.delivery_failures import classify_connection_failure
from tg_lottery.telegram.gateway import GatewayResult, TelegramGateway
from tg_lottery.telegram.gateway_requests import GatewayCall
from tg_lottery.telegram.outbox_payloads import InviteNameFactory, normalize_pending_invite_name
from tg_lottery.telegram.outbox_preflight import (
    PROJECTED_OPERATIONS,
    prepare_call,
    prepare_owned_delivery,
)
from tg_lottery.telegram.outbox_projection import project_outbox_success
from tg_lottery.telegram.outbox_records import (
    add_delivery_attempt,
    delivery_log_context,
    sanitized_remote_result,
    state_after_explicit_failure,
)

_CLAIM_LEASE_SECONDS = 120
_LEASE_RENEW_INTERVAL_SECONDS = 30
_IDLE_WAIT_SECONDS = 0.5
_EXPLICIT_ERRORS = (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError)
_UNKNOWN_ERRORS = (TelegramNetworkError, TelegramServerError, TimeoutError, ConnectionError)
_PROJECTION_RETRY_SECONDS = 30


class OutboxWorker:
    def __init__(
        self,
        *,
        owner_id: str,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: TelegramGateway,
        cipher: SecretCipher,
        lease_seconds: int = _CLAIM_LEASE_SECONDS,
        renew_interval_seconds: float = _LEASE_RENEW_INTERVAL_SECONDS,
        invite_name_factory: InviteNameFactory | None = None,
        bot_id: int | None = None,
    ) -> None:
        if lease_seconds <= 0 or not 0 < renew_interval_seconds < lease_seconds:
            raise ValueError("outbox lease renewal interval must be shorter than the lease")
        self._owner_id = owner_id
        self._session_factory = session_factory
        self._gateway = gateway
        self._cipher = cipher
        self._lease_seconds = lease_seconds
        self._renew_interval_seconds = renew_interval_seconds
        self._invite_name_factory = invite_name_factory
        self._bot_id = bot_id
        self._logger = logging.getLogger(__name__)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._recover_ambiguous()
            await self._project_pending_confirmations()
            outbox = await self._claim_next()
            if outbox is None:
                await self._wait(stop)
                continue
            await self._deliver(outbox)

    async def process_once(self) -> bool:
        await self._recover_ambiguous()
        projected = await self._project_pending_confirmations()
        outbox = await self._claim_next()
        if outbox is None:
            return projected > 0
        await self._deliver(outbox)
        return True

    async def _deliver(self, outbox: TelegramOutbox) -> None:
        prepared = await prepare_owned_delivery(self, outbox)
        if prepared is None:
            return
        call, attempt_number = prepared
        log_context = delivery_log_context(outbox, attempt_number)
        try:
            result = await self._execute_gateway(outbox.id, call)
        except TelegramRetryAfter as exc:
            await self._retry(
                outbox.id,
                attempt_number,
                exc,
                delay_seconds=exc.retry_after,
                error_code="retry_after",
            )
            self._logger.warning(
                "Telegram delivery rate limited",
                extra=log_context
                | {"certainty": "explicit_rejection", "error_code": "retry_after"},
            )
        except _EXPLICIT_ERRORS as exc:
            await self._pre_accept_failed(outbox.id, attempt_number, exc)
            self._logger.warning(
                "Telegram delivery explicitly rejected",
                extra=log_context
                | {"certainty": "explicit_rejection", "error_code": type(exc).__name__},
            )
        except _UNKNOWN_ERRORS as exc:
            await self._network_failure(outbox, attempt_number, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.exception(
                "unexpected Telegram gateway failure",
                extra=log_context | {"certainty": "unknown", "error_code": type(exc).__name__},
            )
            await self._unknown(outbox.id, attempt_number, exc)
        else:
            await self._confirm(outbox.id, attempt_number, result)
            self._logger.info(
                "Telegram delivery confirmed",
                extra=log_context | {"certainty": "confirmed"},
            )

    async def _network_failure(
        self, outbox: TelegramOutbox, attempt_number: int, exc: Exception
    ) -> None:
        context = delivery_log_context(outbox, attempt_number) | {"error_code": type(exc).__name__}
        proven, delay = await classify_connection_failure(self._session_factory, outbox, exc)
        if proven:
            if delay is not None:
                await self._retry(
                    outbox.id,
                    attempt_number,
                    exc,
                    delay_seconds=delay,
                    error_code=type(exc).__name__,
                    preserve_error=True,
                )
                self._logger.warning(
                    "Telegram connection failed before request; retry scheduled",
                    extra=context | {"certainty": "explicit_rejection", "retry_after": delay},
                )
                return
            await self._pre_accept_failed(outbox.id, attempt_number, exc)
            self._logger.error(
                "Telegram request failed before connection; request was not sent",
                extra=context | {"certainty": "explicit_rejection", "transport_phase": "connect"},
            )
            return
        await self._unknown(outbox.id, attempt_number, exc)
        self._logger.error(
            "Telegram delivery certainty unknown",
            extra=context | {"certainty": "unknown"},
        )

    async def _prepare_gateway_call(self, outbox: TelegramOutbox) -> GatewayCall | None:
        try:
            return await prepare_call(self._session_factory, outbox)
        except ValueError as exc:
            attempt_number = await self._pre_accept_failed(outbox.id, None, exc, claim=outbox)
            self._logger.warning(
                "Telegram delivery payload rejected before gateway",
                extra=delivery_log_context(outbox, attempt_number)
                | {"certainty": "explicit_rejection", "error_code": type(exc).__name__},
            )
            return None

    async def _execute_gateway(self, outbox_id: UUID, call: GatewayCall) -> GatewayResult:
        return await run_with_heartbeat(
            self._gateway.execute(call.request, call.invite_link),
            lambda: self._renew_claim(outbox_id),
            self._renew_interval_seconds,
        )

    async def _claim_next(self) -> TelegramOutbox | None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(TelegramOutbox)
                .where(
                    or_(
                        TelegramOutbox.state.in_(
                            [OutboxState.PENDING, OutboxState.RETRY_SCHEDULED]
                        ),
                        (
                            (TelegramOutbox.state == OutboxState.CLAIMED)
                            & (TelegramOutbox.lease_until < now)
                        ),
                    ),
                    TelegramOutbox.run_at <= now,
                    TelegramOutbox.gateway_started_at.is_(None),
                )
                .order_by(TelegramOutbox.run_at, TelegramOutbox.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            await apply_retention(session, row)
            normalize_pending_invite_name(row, self._invite_name_factory)
            row.state = OutboxState.CLAIMED
            row.lease_owner = self._owner_id
            row.lease_until = now + timedelta(seconds=self._lease_seconds)
            await session.flush()
            session.expunge(row)
            return row

    async def _renew_claim(self, outbox_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_owned_claim(session, outbox_id)
            row.lease_until = utcnow() + timedelta(seconds=self._lease_seconds)

    async def _mark_gateway_started(self, outbox_id: UUID, *, claim=None) -> int:
        return await start_claimed_gateway(
            self._session_factory, outbox_id, self._owner_id, claim=claim
        )

    async def _confirm(self, outbox_id: UUID, attempt_number: int, result: GatewayResult) -> None:
        await self._store_confirmation(outbox_id, attempt_number, result)
        await self._try_project_confirmation(outbox_id)

    async def _store_confirmation(
        self, outbox_id: UUID, attempt_number: int, result: GatewayResult
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_owned_claim(session, outbox_id)
            add_delivery_attempt(
                session,
                row,
                attempt_number=attempt_number,
                certainty=DeliveryCertainty.CONFIRMED,
                remote_result=sanitized_remote_result(result.remote_result),
            )
            row.state = OutboxState.CONFIRMED
            row.remote_message_id = result.remote_message_id
            if result.invite_link is not None:
                row.encrypted_remote_secret = self._cipher.encrypt(
                    result.invite_link,
                    context=f"outbox-result:{row.id}",
                )
            if (
                row.operation not in PROJECTED_OPERATIONS
                and "delete_after_seconds" not in row.payload
            ):
                row.projected_at = utcnow()
            row.last_error = None
            row.lease_owner = None
            row.lease_until = None

    async def _retry(
        self,
        outbox_id: UUID,
        attempt_number: int,
        exc: Exception,
        *,
        delay_seconds: int,
        error_code: str,
        preserve_error: bool = False,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_owned_claim(session, outbox_id)
            add_delivery_attempt(
                session,
                row,
                attempt_number=attempt_number,
                certainty=DeliveryCertainty.EXPLICIT_REJECTION,
                error_code=error_code,
                error_description=str(exc),
            )
            row.state = OutboxState.RETRY_SCHEDULED
            row.run_at = utcnow() + timedelta(seconds=delay_seconds)
            row.gateway_started_at = None
            row.lease_owner = None
            row.lease_until = None
            if preserve_error:
                row.last_error = str(exc)[:4000]

    async def _pre_accept_failed(
        self, outbox_id: UUID, attempt_number: int | None, exc: Exception, *, claim=None
    ) -> int:
        async with self._session_factory() as session, session.begin():
            if claim is not None:
                row = await lock_presend_claim(session, claim)
            else:
                row = await self._locked_owned_claim(session, outbox_id)
            if attempt_number is None:
                count = await session.scalar(
                    select(func.count(DeliveryAttempt.id)).where(
                        DeliveryAttempt.outbox_id == outbox_id
                    )
                )
                attempt_number = int(count or 0) + 1
            add_delivery_attempt(
                session,
                row,
                attempt_number=attempt_number,
                certainty=DeliveryCertainty.EXPLICIT_REJECTION,
                error_code=type(exc).__name__,
                error_description=str(exc),
            )
            row.state = await state_after_explicit_failure(session, row)
            row.last_error = str(exc)[:4000]
            row.lease_owner = None
            row.lease_until = None
            await record_outbox_target_block(
                session,
                row,
                error_code=type(exc).__name__,
                details=str(exc),
            )
            if (
                self._bot_id
                and row.recipient_chat_id > 0
                and isinstance(exc, TelegramForbiddenError)
            ):
                if "bot was blocked by the user" in str(exc).casefold():
                    await record_blocked_delivery(
                        session,
                        bot_id=self._bot_id,
                        user_id=row.recipient_chat_id,
                        observed_at=utcnow(),
                    )
            return attempt_number

    async def _unknown(self, outbox_id: UUID, attempt_number: int, exc: Exception) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_owned_claim(session, outbox_id)
            add_delivery_attempt(
                session,
                row,
                attempt_number=attempt_number,
                certainty=DeliveryCertainty.UNKNOWN,
                error_code=type(exc).__name__,
                error_description=str(exc),
            )
            row.state = OutboxState.UNKNOWN_HOLD
            row.last_error = str(exc)[:4000]
            row.lease_owner = None
            row.lease_until = None
            await record_outbox_target_block(
                session,
                row,
                error_code=type(exc).__name__,
                details=str(exc),
            )

    async def _recover_ambiguous(self) -> None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(TelegramOutbox)
                    .where(
                        TelegramOutbox.state == OutboxState.CLAIMED,
                        TelegramOutbox.lease_until < now,
                        TelegramOutbox.gateway_started_at.is_not(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                count = await session.scalar(
                    select(func.count(DeliveryAttempt.id)).where(
                        DeliveryAttempt.outbox_id == row.id
                    )
                )
                add_delivery_attempt(
                    session,
                    row,
                    attempt_number=int(count or 0) + 1,
                    certainty=DeliveryCertainty.UNKNOWN,
                    error_code="worker_lost_after_gateway_start",
                    error_description="lease expired after gateway_started_at",
                )
                row.state = OutboxState.UNKNOWN_HOLD
                row.lease_owner = None
                row.lease_until = None
                await record_outbox_target_block(
                    session,
                    row,
                    error_code="worker_lost_after_gateway_start",
                    details="lease expired after gateway_started_at",
                )

    async def _project_pending_confirmations(self) -> int:
        async with self._session_factory() as session:
            row_ids = (
                await session.scalars(
                    select(TelegramOutbox.id)
                    .where(
                        TelegramOutbox.state == OutboxState.CONFIRMED,
                        TelegramOutbox.projected_at.is_(None),
                        TelegramOutbox.run_at <= utcnow(),
                    )
                    .order_by(TelegramOutbox.updated_at)
                )
            ).all()
        projected = 0
        for row_id in row_ids:
            projected += int(await self._try_project_confirmation(row_id))
        return projected

    async def _try_project_confirmation(self, outbox_id: UUID) -> bool:
        try:
            await self._project_confirmation(outbox_id)
        except Exception as exc:
            self._logger.exception(
                "confirmed Telegram result projection failed",
                extra={"outbox_id": str(outbox_id), "certainty": "confirmed"},
            )
            await self._record_projection_failure(outbox_id, exc)
            return False
        return True

    async def _project_confirmation(self, outbox_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked(session, outbox_id)
            if row.state is not OutboxState.CONFIRMED:
                raise RuntimeError("only confirmed outbox rows can be projected")
            if row.projected_at is not None:
                return
            await project_outbox_success(
                session, row, self._stored_result(row), cipher=self._cipher
            )
            await resolve_outbox_target_block(session, row)
            row.projected_at = utcnow()
            row.encrypted_remote_secret = None
            row.last_error = None

    async def _record_projection_failure(self, outbox_id: UUID, exc: Exception) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked(session, outbox_id)
            if row.state is OutboxState.CONFIRMED and row.projected_at is None:
                row.last_error = f"projection: {type(exc).__name__}: {exc}"[:4000]
                row.run_at = utcnow() + timedelta(seconds=_PROJECTION_RETRY_SECONDS)

    def _stored_result(self, row: TelegramOutbox) -> GatewayResult:
        secret = None
        if row.encrypted_remote_secret is not None:
            secret = self._cipher.decrypt(
                row.encrypted_remote_secret,
                context=f"outbox-result:{row.id}",
            )
        return GatewayResult(
            remote_result={},
            remote_message_id=row.remote_message_id,
            invite_link=secret,
        )

    async def _locked(self, session: AsyncSession, outbox_id: UUID) -> TelegramOutbox:
        row = await session.get(TelegramOutbox, outbox_id, with_for_update=True)
        if row is None:
            raise RuntimeError("outbox row disappeared")
        return row

    async def _locked_owned_claim(self, session: AsyncSession, outbox_id: UUID) -> TelegramOutbox:
        row = await self._locked(session, outbox_id)
        if row.state is not OutboxState.CLAIMED or row.lease_owner != self._owner_id:
            raise RuntimeError(
                f"outbox claim ownership lost: outbox={row.id} owner={self._owner_id} "
                f"current_owner={row.lease_owner} state={row.state}"
            )
        return row

    async def _wait(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=_IDLE_WAIT_SECONDS)
        except TimeoutError:
            return
