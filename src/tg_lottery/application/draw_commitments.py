from __future__ import annotations

import secrets
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.campaign_targets import (
    participation_chat_ids,
    requires_draw_commitment,
)
from tg_lottery.application.records import add_audit, add_outbox
from tg_lottery.domain.enums import OutboxState
from tg_lottery.domain.errors import SettlementBlocked
from tg_lottery.infrastructure.db import Campaign, DrawCommitment, TelegramOutbox

COMMITMENT_ALGORITHM_VERSION = "sha256-commit-v1"
_COMMITMENT_DOMAIN = "tg-lottery"


def commitment_hash(campaign_id: UUID, seed: str) -> str:
    value = f"{_COMMITMENT_DOMAIN}:{COMMITMENT_ALGORITHM_VERSION}:{campaign_id}:{seed}"
    return sha256(value.encode()).hexdigest()


async def ensure_draw_commitment(
    session: object, campaign: Campaign, committed_at: datetime
) -> DrawCommitment | None:
    if not requires_draw_commitment(campaign):
        return None
    existing = await session.scalar(
        select(DrawCommitment).where(DrawCommitment.campaign_id == campaign.id)
    )
    if existing is not None:
        _validate_commitment(existing)
        return existing
    seed = secrets.token_hex(32)
    commitment = DrawCommitment(
        campaign_id=campaign.id,
        algorithm_version=COMMITMENT_ALGORITHM_VERSION,
        seed=seed,
        commitment_hash=commitment_hash(campaign.id, seed),
        committed_at=committed_at,
    )
    session.add(commitment)
    await session.flush()
    await _queue_commitment_messages(session, campaign, commitment)
    add_audit(
        session,
        actor_user_id=None,
        action="draw.commitment_created",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        details={"commitment_hash": commitment.commitment_hash},
    )
    return commitment


async def require_commitment_confirmed(session: object, campaign: Campaign) -> None:
    if not requires_draw_commitment(campaign):
        return
    commitment = await session.scalar(
        select(DrawCommitment).where(DrawCommitment.campaign_id == campaign.id)
    )
    if commitment is None:
        raise SettlementBlocked("draw_commitment_missing")
    _validate_commitment(commitment)
    expected = len(await participation_chat_ids(session, campaign.id, confirmed_only=True))
    if expected == 0:
        raise SettlementBlocked("draw_commitment_targets_missing")
    confirmed = await session.scalar(
        select(func.count(TelegramOutbox.id)).where(
            TelegramOutbox.aggregate_type == "campaign",
            TelegramOutbox.aggregate_id == str(campaign.id),
            TelegramOutbox.operation == "send_draw_commitment",
            TelegramOutbox.state == OutboxState.CONFIRMED,
        )
    )
    if int(confirmed or 0) != expected:
        raise SettlementBlocked("draw_commitment_delivery_pending")


async def draw_seed(session: object, campaign: Campaign, revealed_at: datetime) -> str:
    if not requires_draw_commitment(campaign):
        return secrets.token_hex(32)
    commitment = await session.scalar(
        select(DrawCommitment).where(DrawCommitment.campaign_id == campaign.id)
    )
    if commitment is None:
        raise ValueError("campaign draw commitment is missing")
    _validate_commitment(commitment)
    commitment.revealed_at = commitment.revealed_at or revealed_at
    return commitment.seed


async def campaign_commitment(session: object, campaign_id: UUID) -> DrawCommitment | None:
    return await session.scalar(
        select(DrawCommitment).where(DrawCommitment.campaign_id == campaign_id)
    )


async def _queue_commitment_messages(
    session: object, campaign: Campaign, commitment: DrawCommitment
) -> None:
    chat_ids = await participation_chat_ids(session, campaign.id, confirmed_only=True)
    if not chat_ids:
        raise ValueError("draw commitment requires confirmed participation groups")
    for chat_id in chat_ids:
        add_outbox(
            session,
            operation="send_draw_commitment",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=chat_id,
            business_key=f"draw_commitment:{campaign.id}:{chat_id}",
            payload={
                "campaign_name": campaign.name,
                "campaign_ref": campaign.public_ref,
                "algorithm_version": commitment.algorithm_version,
                "commitment_hash": commitment.commitment_hash,
                "committed_at": commitment.committed_at.isoformat(),
            },
        )


def _validate_commitment(commitment: DrawCommitment) -> None:
    if commitment.algorithm_version != COMMITMENT_ALGORITHM_VERSION:
        raise ValueError("unsupported draw commitment algorithm")
    expected = commitment_hash(commitment.campaign_id, commitment.seed)
    if not secrets.compare_digest(expected, commitment.commitment_hash):
        raise ValueError("draw commitment integrity check failed")
