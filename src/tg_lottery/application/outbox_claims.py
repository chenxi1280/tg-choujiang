"""Validate a detached claim again under the Outbox row lock before sending."""

from sqlalchemy import func, select

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import DeliveryAttempt, TelegramOutbox


class OutboxClaimLost(RuntimeError):
    """The caller can no longer change or send this claimed operation."""


def validate_presend_claim(row, claim):
    if row is None or not _same_claim(row, claim) or not _live_presend_claim(row):
        raise OutboxClaimLost(f"outbox pre-send claim no longer current: {claim.id}")


def _same_claim(row, claim):
    return (
        claim.state == OutboxState.CLAIMED
        and row.state == claim.state
        and row.id == claim.id
        and bool(row.lease_owner)
        and row.lease_owner == claim.lease_owner
        and row.lease_until == claim.lease_until
    )


def _live_presend_claim(row):
    return (
        row.lease_until is not None
        and row.lease_until > utcnow()
        and row.gateway_started_at is None
    )


async def lock_presend_claim(session, claim):
    row = await session.get(TelegramOutbox, claim.id, with_for_update=True, populate_existing=True)
    validate_presend_claim(row, claim)
    return row


async def start_claimed_gateway(sessions, outbox_id, owner_id, *, claim=None):
    async with sessions() as session, session.begin():
        row = await session.get(TelegramOutbox, outbox_id, with_for_update=True)
        if row is None or row.lease_owner != owner_id:
            raise OutboxClaimLost(f"outbox claim ownership lost: {outbox_id}")
        validate_presend_claim(row, claim if claim is not None else row)
        row.gateway_started_at = utcnow()
        count = await session.scalar(
            select(func.count(DeliveryAttempt.id)).where(DeliveryAttempt.outbox_id == outbox_id)
        )
        return int(count or 0) + 1
