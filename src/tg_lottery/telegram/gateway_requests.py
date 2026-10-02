from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts, load_campaign_outcome
from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import (
    Campaign,
    DrawRun,
    DrawSnapshot,
    InviteLink,
    Participant,
    TelegramOutbox,
    Winner,
)
from tg_lottery.telegram.member_bonus_text import historical_outcome_status
from tg_lottery.telegram.member_group_invites import HIDDEN_INVITE_STATES
from tg_lottery.telegram.member_invites import legacy_invite_note, unavailable_invite_link_note


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
    if outbox.operation == "send_winner_notice" and "issuer_user_id" not in payload:
        payload = payload | await _winner_notice_issuer(session_factory, outbox)
    if outbox.operation == "send_invite_link":
        payload = payload | await _invite_delivery_projection(session_factory, outbox)
    return GatewayRequest(
        operation=outbox.operation,
        recipient_chat_id=outbox.recipient_chat_id,
        payload=payload,
    )


async def _invite_delivery_projection(
    session_factory: async_sessionmaker[AsyncSession], outbox: TelegramOutbox
) -> dict[str, Any]:
    link_id = invite_link_id(outbox)
    if outbox.aggregate_type != "invite_link" or link_id != UUID(outbox.aggregate_id):
        raise ValueError("invite delivery must reference its persisted invite link")
    async with session_factory() as session:
        record = (
            await session.execute(
                select(Campaign, InviteLink)
                .join(InviteLink, InviteLink.campaign_id == Campaign.id)
                .join(
                    Participant,
                    (Participant.id == InviteLink.participant_id)
                    & (Participant.campaign_id == Campaign.id),
                )
                .where(InviteLink.id == link_id, Participant.user_id == outbox.recipient_chat_id)
            )
        ).one_or_none()
        if record is None:
            raise ValueError("invite delivery recipient does not match its persisted participant")
        campaign, link = record
        outcome = None
        if campaign.state in HIDDEN_INVITE_STATES:
            outcome = await load_campaign_outcome(session, campaign.id)
        note, active, visible = _invite_entry_status(campaign, link, outcome=outcome)
        return {
            "invite_delivery_note": note,
            "invite_bonus_active": active,
            "invite_entry_visible": visible,
            "campaign_name": campaign.name,
            "campaign_ref": campaign.public_ref,
        }


def _invite_entry_status(
    campaign: Campaign, link: InviteLink, *, outcome: CampaignOutcomeFacts | None = None
) -> tuple[str, bool, bool]:
    historical_status = historical_outcome_status(campaign, outcome=outcome)
    if historical_status is not None:
        return f"{historical_status}，邀请入口已隐藏；历史邀请记录和已确认加成保留。", False, False
    unavailable = unavailable_invite_link_note(link, now=utcnow())
    if unavailable is not None:
        return unavailable, False, False
    note, active = legacy_invite_note(campaign)
    return note, active, True


async def _winner_notice_issuer(
    session_factory: async_sessionmaker[AsyncSession], outbox: TelegramOutbox
) -> dict[str, Any]:
    if outbox.aggregate_type != "winner":
        raise ValueError("winner notice must reference a persisted winner")
    async with session_factory() as session:
        campaign = await session.scalar(
            select(Campaign)
            .join(DrawSnapshot, DrawSnapshot.campaign_id == Campaign.id)
            .join(DrawRun, DrawRun.snapshot_id == DrawSnapshot.id)
            .join(Winner, Winner.draw_run_id == DrawRun.id)
            .join(Participant, Participant.id == Winner.participant_id)
            .where(
                Winner.id == UUID(outbox.aggregate_id),
                Participant.user_id == outbox.recipient_chat_id,
            )
        )
        if campaign is None:
            raise ValueError("winner notice recipient does not match a persisted winner")
        presentation = campaign.presentation or {}
        return {
            "issuer_user_id": presentation.get("issuer_user_id") or campaign.created_by,
            "issuer_username": presentation.get("issuer_username_at_publish"),
        }


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
