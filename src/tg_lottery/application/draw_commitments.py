from __future__ import annotations

import secrets
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.campaign_targets import (
    requires_draw_commitment,
    restore_confirmed_participation_targets,
)
from tg_lottery.application.records import add_audit
from tg_lottery.domain.errors import SettlementBlocked
from tg_lottery.infrastructure.db import Campaign, DrawCommitment

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
    await restore_confirmed_participation_targets(session, campaign.id)
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
    await restore_confirmed_participation_targets(session, campaign.id)
    commitment = await session.scalar(
        select(DrawCommitment).where(DrawCommitment.campaign_id == campaign.id)
    )
    if commitment is None:
        raise SettlementBlocked("draw_commitment_missing")
    _validate_commitment(commitment)


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


def _validate_commitment(commitment: DrawCommitment) -> None:
    if commitment.algorithm_version != COMMITMENT_ALGORITHM_VERSION:
        raise ValueError("unsupported draw commitment algorithm")
    expected = commitment_hash(commitment.campaign_id, commitment.seed)
    if not secrets.compare_digest(expected, commitment.commitment_hash):
        raise ValueError("draw commitment integrity check failed")
