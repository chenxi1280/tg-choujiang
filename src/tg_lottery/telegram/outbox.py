import asyncio
import logging
from datetime import datetime, timedelta
from hashlib import sha256
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

from tg_lottery.application.campaign_notifications import add_cancelled_campaign_card
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.application.required_chat_links import project_required_chat_operation
from tg_lottery.domain.enums import CampaignState, DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import (
    Campaign,
    DeliveryAttempt,
    InviteLink,
    Participant,
    TelegramOutbox,
)
from tg_lottery.infrastructure.heartbeat import run_with_heartbeat
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult, TelegramGateway
from tg_lottery.telegram.gateway_requests import GatewayCall, build_gateway_call, invite_link_id
from tg_lottery.telegram.outbox_payloads import InviteNameFactory, normalize_pending_invite_name
from tg_lottery.telegram.outbox_records import (
    add_delivery_attempt,
    delivery_log_context,
    sanitized_remote_result,
)

_CLAIM_LEASE_SECONDS = 120
_LEASE_RENEW_INTERVAL_SECONDS = 30
_IDLE_WAIT_SECONDS = 0.5
_EXPLICIT_ERRORS = (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError)
_UNKNOWN_ERRORS = (TelegramNetworkError, TelegramServerError, TimeoutError, ConnectionError)
_PROJECTED_OPERATIONS = frozenset(
    {
        "send_campaign_card",
        "create_invite_link",
        "revoke_invite_link",
        "create_required_chat_invite",
        "revoke_required_chat_invite",
    }
)
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
        call = await self._prepare_gateway_call(outbox)
        if call is None:
            return
        attempt_number = await self._mark_gateway_started(outbox.id)
        log_context = delivery_log_context(outbox, attempt_number)
        try:
            result = await self._execute_gateway(outbox.id, call)
        except TelegramRetryAfter as exc:
            await self._retry(outbox.id, attempt_number, exc)
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
        except ValueError as exc:
            await self._pre_accept_failed(outbox.id, attempt_number, exc)
            self._logger.warning(
                "Telegram delivery payload rejected",
                extra=log_context
                | {"certainty": "explicit_rejection", "error_code": type(exc).__name__},
            )
        except _UNKNOWN_ERRORS as exc:
            await self._unknown(outbox.id, attempt_number, exc)
            self._logger.error(
                "Telegram delivery certainty unknown",
                extra=log_context | {"certainty": "unknown", "error_code": type(exc).__name__},
            )
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

    async def _prepare_gateway_call(self, outbox: TelegramOutbox) -> GatewayCall | None:
        try:
            return await build_gateway_call(self._session_factory, outbox)
        except ValueError as exc:
            attempt_number = await self._pre_accept_failed(outbox.id, None, exc)
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

    async def _mark_gateway_started(self, outbox_id: UUID) -> int:
        async with self._session_factory() as session, session.begin():
            row = await self._locked(session, outbox_id)
            if row.state is not OutboxState.CLAIMED or row.lease_owner != self._owner_id:
                raise RuntimeError("outbox claim ownership was lost")
            if row.gateway_started_at is not None:
                raise RuntimeError("gateway was already started for this mutation")
            row.gateway_started_at = utcnow()
            count = await session.scalar(
                select(func.count(DeliveryAttempt.id)).where(DeliveryAttempt.outbox_id == outbox_id)
            )
            return int(count or 0) + 1

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
            if row.operation not in _PROJECTED_OPERATIONS:
                row.projected_at = utcnow()
            row.last_error = None
            row.lease_owner = None
            row.lease_until = None

    async def _retry(self, outbox_id: UUID, attempt_number: int, exc: TelegramRetryAfter) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_owned_claim(session, outbox_id)
            add_delivery_attempt(
                session,
                row,
                attempt_number=attempt_number,
                certainty=DeliveryCertainty.EXPLICIT_REJECTION,
                error_code="retry_after",
                error_description=str(exc),
            )
            row.state = OutboxState.RETRY_SCHEDULED
            row.run_at = utcnow() + timedelta(seconds=exc.retry_after)
            row.gateway_started_at = None
            row.lease_owner = None
            row.lease_until = None

    async def _pre_accept_failed(
        self, outbox_id: UUID, attempt_number: int | None, exc: Exception
    ) -> int:
        async with self._session_factory() as session, session.begin():
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
            row.state = OutboxState.PRE_ACCEPT_FAILED
            row.last_error = str(exc)[:4000]
            row.lease_owner = None
            row.lease_until = None
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
            await self._project_success(session, row, self._stored_result(row))
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

    async def _project_success(
        self, session: AsyncSession, row: TelegramOutbox, result: GatewayResult
    ) -> None:
        if await project_required_chat_operation(session, row, result, cipher=self._cipher):
            return
        if row.operation == "send_campaign_card":
            await self._project_campaign_card(session, row)
            return
        if row.operation == "create_invite_link":
            await self._project_invite_link(session, row, result)
            return
        if row.operation == "revoke_invite_link":
            link = await self._required_link(session, row)
            link.revoked_at = utcnow()

    async def _project_campaign_card(self, session: AsyncSession, row: TelegramOutbox) -> None:
        campaign = await session.get(Campaign, UUID(row.aggregate_id), with_for_update=True)
        if campaign is None:
            raise RuntimeError("published campaign disappeared")
        if campaign.published_message_id == row.remote_message_id and campaign.state in {
            CampaignState.SCHEDULED,
            CampaignState.RUNNING,
        }:
            return
        if campaign.state is CampaignState.CANCELLED:
            campaign.published_message_id = row.remote_message_id
            add_cancelled_campaign_card(session, campaign, row.recipient_chat_id)
            return
        if campaign.state is not CampaignState.PUBLISHING:
            raise RuntimeError("campaign publication projection has invalid state")
        campaign.published_message_id = row.remote_message_id
        campaign.state = (
            CampaignState.RUNNING if utcnow() >= campaign.preheat_at else CampaignState.SCHEDULED
        )

    async def _project_invite_link(
        self, session: AsyncSession, row: TelegramOutbox, result: GatewayResult
    ) -> None:
        participant_id = UUID(str(row.payload["participant_id"]))
        existing = await session.scalar(
            select(InviteLink).where(InviteLink.participant_id == participant_id)
        )
        if existing:
            return
        if result.invite_link is None:
            raise RuntimeError("Telegram success omitted encrypted invite_link")
        link = InviteLink(
            campaign_id=UUID(str(row.payload["campaign_id"])),
            participant_id=participant_id,
            link_hash=sha256(result.invite_link.encode()).hexdigest(),
            telegram_name=str(row.payload["name"]),
            expire_at=datetime.fromisoformat(str(row.payload["expire_at"])),
            remote_created_at=utcnow(),
        )
        session.add(link)
        await session.flush()
        link.encrypted_url = self._cipher.encrypt(
            result.invite_link,
            context=f"invite-link:{link.id}",
        )
        participant = await session.get(Participant, participant_id)
        if participant is None:
            raise RuntimeError("invite link participant disappeared")
        add_outbox(
            session,
            operation="send_invite_link",
            aggregate_type="invite_link",
            aggregate_id=str(link.id),
            recipient_chat_id=participant.user_id,
            business_key=f"send_invite_link:{link.id}:{participant.user_id}",
            payload={"invite_link_id": str(link.id)},
        )

    async def _required_link(self, session: AsyncSession, outbox: TelegramOutbox) -> InviteLink:
        link_id = invite_link_id(outbox)
        link = await session.get(InviteLink, link_id, with_for_update=True) if link_id else None
        if link is None:
            raise RuntimeError("invite link projection target is missing")
        return link

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
