import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from aiogram import Bot
from aiogram.enums import UpdateType
from aiogram.types import Update
from aiogram.utils.serialization import deserialize_telegram_object_to_python
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_references import resolve_campaign_reference
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import BotRuntimeLease, PollingOffset, TelegramUpdate

ALLOWED_UPDATES = [update_type.value for update_type in UpdateType]
_LEASE_RETRY_SECONDS = 5


@dataclass(frozen=True)
class PollingCursor:
    next_offset: int | None


class TelegramPollingAdapter:
    def __init__(
        self,
        *,
        bot: Bot,
        bot_id: int,
        owner_id: str,
        session_factory: async_sessionmaker[AsyncSession],
        timeout_seconds: int,
        lease_seconds: int,
    ) -> None:
        self._bot = bot
        self._bot_id = bot_id
        self._owner_id = owner_id
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds
        self._lease_seconds = lease_seconds
        self._logger = logging.getLogger(__name__)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                if not await self._acquire_or_renew_lease():
                    await self._wait(stop, _LEASE_RETRY_SECONDS)
                    continue
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("polling iteration failed", extra={"bot_id": self._bot_id})
                await self._wait(stop, _LEASE_RETRY_SECONDS)

    async def poll_once(self) -> int:
        cursor = await self._durable_cursor()
        updates = await self._bot.get_updates(
            offset=cursor.next_offset,
            timeout=self._timeout_seconds,
            allowed_updates=ALLOWED_UPDATES,
        )
        await self._persist_batch(updates, cursor)
        if updates:
            self._logger.info(
                "polling batch committed",
                extra={
                    "bot_id": self._bot_id,
                    "first_update_id": updates[0].update_id,
                    "update_id": updates[-1].update_id,
                    "batch_size": len(updates),
                },
            )
        return len(updates)

    async def _durable_cursor(self) -> PollingCursor:
        async with self._session_factory() as session:
            last_id = await session.scalar(
                select(PollingOffset.last_durable_update_id).where(
                    PollingOffset.bot_id == self._bot_id
                )
            )
            return PollingCursor(last_id + 1 if last_id is not None else None)

    async def _persist_batch(self, updates: Sequence[Update], cursor: PollingCursor) -> None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            for update in updates:
                payload = _serialize_update(update)
                statement = insert(TelegramUpdate).values(
                    bot_id=self._bot_id,
                    update_id=update.update_id,
                    update_type=_update_type(update),
                    routing_chat_id=_routing_chat_id(update),
                    routing_campaign_id=await _routing_campaign_id(session, update),
                    event_at=_event_at(update, now),
                    payload=payload,
                    payload_hash=_payload_hash(payload),
                    state=UpdateState.RECEIVED,
                    attempts=0,
                )
                await session.execute(
                    statement.on_conflict_do_nothing(index_elements=["bot_id", "update_id"])
                )
            last_update_id = updates[-1].update_id if updates else None
            await self._upsert_offset(
                session,
                last_update_id=last_update_id,
                now=now,
                cursor=cursor,
                updates=updates,
            )
            await self._renew_owned_lease(session, now)

    async def _upsert_offset(
        self,
        session: AsyncSession,
        *,
        last_update_id: int | None,
        now: datetime,
        cursor: PollingCursor,
        updates: Sequence[Update],
    ) -> None:
        values: dict[str, Any] = {
            "bot_id": self._bot_id,
            "last_poll_succeeded_at": now,
            "updated_at": now,
        }
        if last_update_id is not None:
            values["last_durable_update_id"] = last_update_id
        gap = _update_gap(cursor.next_offset, updates)
        if gap is not None:
            values.update(_gap_values(gap, now))
            self._logger.warning(
                "Telegram update gap observed",
                extra={"bot_id": self._bot_id, "update_id": gap[1]},
            )
        statement = insert(PollingOffset).values(**values)
        update_values = {key: value for key, value in values.items() if key != "bot_id"}
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["bot_id"],
                set_=update_values,
            )
        )

    async def _acquire_or_renew_lease(self) -> bool:
        now = utcnow()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        statement = insert(BotRuntimeLease).values(
            bot_id=self._bot_id,
            owner_id=self._owner_id,
            fence=1,
            lease_until=lease_until,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["bot_id"],
            set_={
                "owner_id": self._owner_id,
                "fence": BotRuntimeLease.fence + 1,
                "lease_until": lease_until,
                "updated_at": now,
            },
            where=or_(
                BotRuntimeLease.owner_id == self._owner_id,
                BotRuntimeLease.lease_until < now,
            ),
        ).returning(BotRuntimeLease.owner_id)
        async with self._session_factory() as session, session.begin():
            owner = await session.scalar(statement)
            return owner == self._owner_id

    async def _renew_owned_lease(self, session: AsyncSession, now: datetime) -> None:
        lease_until = now + timedelta(seconds=self._lease_seconds)
        lease = await session.scalar(
            select(BotRuntimeLease)
            .where(
                BotRuntimeLease.bot_id == self._bot_id,
                BotRuntimeLease.owner_id == self._owner_id,
            )
            .with_for_update()
        )
        if lease is None:
            raise RuntimeError("polling lease ownership was lost before durable commit")
        lease.lease_until = lease_until
        lease.updated_at = now

    async def _wait(self, stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            return


def _payload_hash(payload: dict[str, Any]) -> str:
    import json

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _serialize_update(update: Update) -> dict[str, Any]:
    payload = deserialize_telegram_object_to_python(
        update,
        include_api_method_name=False,
    )
    if not isinstance(payload, dict):
        raise TypeError("serialized Telegram update must be an object")
    return payload


def _update_type(update: Update) -> str:
    return update.event_type


def _event_at(update: Update, received_at: datetime) -> datetime:
    event = update.event
    event_date = getattr(event, "date", None)
    if event_date is not None:
        return event_date
    message = getattr(event, "message", None)
    message_date = getattr(message, "date", None)
    return message_date or received_at


def _routing_chat_id(update: Update) -> int | None:
    event = update.event
    chat = getattr(event, "chat", None)
    if chat is not None:
        return int(chat.id)
    message = getattr(event, "message", None)
    message_chat = getattr(message, "chat", None)
    return int(message_chat.id) if message_chat is not None else None


async def _routing_campaign_id(session: AsyncSession, update: Update) -> str | None:
    reference = _routing_campaign_reference(update)
    if reference is None:
        return None
    campaign = await resolve_campaign_reference(session, reference)
    return str(campaign.id) if campaign else None


def _routing_campaign_reference(update: Update) -> str | None:
    message = update.message
    if message is not None and message.chat.type == "private":
        value = _reference_suffix((message.text or "").strip(), "/start join_")
        if value is not None:
            return value
    query = update.callback_query
    return _reference_suffix(query.data or "", "member:verify:") if query is not None else None


def _reference_suffix(value: str, prefix: str) -> str | None:
    if not value.startswith(prefix):
        return None
    reference = value.removeprefix(prefix)
    return reference or None


def _update_gap(next_offset: int | None, updates: Sequence[Update]) -> tuple[int, int] | None:
    if next_offset is None or not updates or updates[0].update_id <= next_offset:
        return None
    return next_offset, updates[0].update_id - 1


def _gap_values(gap: tuple[int, int], now: datetime) -> dict[str, object]:
    return {
        "integrity_status": "integrity_review_required",
        "last_gap_from": gap[0],
        "last_gap_to": gap[1],
        "last_gap_detected_at": now,
    }
