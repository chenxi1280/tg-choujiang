from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.infrastructure.db import Campaign, InviteLink, TelegramOutbox


@dataclass(frozen=True)
class GatewayRequest:
    operation: str
    recipient_chat_id: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class GatewayCall:
    request: GatewayRequest
    invite_link: InviteLink | None


async def build_gateway_call(
    session_factory: async_sessionmaker[AsyncSession],
    outbox: TelegramOutbox,
) -> GatewayCall:
    request = await build_gateway_request(session_factory, outbox)
    link_id = invite_link_id(outbox)
    if link_id is None:
        return GatewayCall(request, None)
    async with session_factory() as session:
        link = await session.get(InviteLink, link_id)
        if link:
            session.expunge(link)
    return GatewayCall(request, link)


async def build_gateway_request(
    session_factory: async_sessionmaker[AsyncSession],
    outbox: TelegramOutbox,
) -> GatewayRequest:
    payload = dict(outbox.payload)
    if outbox.operation == "send_campaign_card":
        campaign = await _campaign_projection(session_factory, outbox, payload)
        payload["timezone"] = _payload_timezone(payload) or campaign.timezone
        payload["campaign_ref"] = campaign.public_ref
    return GatewayRequest(
        operation=outbox.operation,
        recipient_chat_id=outbox.recipient_chat_id,
        payload=payload,
    )


async def _campaign_projection(
    session_factory: async_sessionmaker[AsyncSession],
    outbox: TelegramOutbox,
    payload: dict[str, Any],
) -> Campaign:
    campaign_id = UUID(str(payload.get("campaign_id") or outbox.aggregate_id))
    async with session_factory() as session:
        campaign = await session.scalar(select(Campaign).where(Campaign.id == campaign_id))
        if campaign:
            session.expunge(campaign)
    if campaign is None:
        raise ValueError(f"campaign projection not found: {campaign_id}")
    return campaign


def _payload_timezone(payload: dict[str, Any]) -> str | None:
    timezone = payload.get("timezone")
    if timezone:
        return str(timezone)
    rules = payload.get("rules")
    if not isinstance(rules, dict) or not rules.get("timezone"):
        return None
    return str(rules["timezone"])


def invite_link_id(outbox: TelegramOutbox) -> UUID | None:
    value = outbox.payload.get("invite_link_id")
    return UUID(str(value)) if value else None
