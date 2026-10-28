"""Classify unknown deliveries without changing their delivery certainty."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select

from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import AuditLog, DeliveryAttempt, TelegramOutbox
from tg_lottery.ops.unknown_review_snapshot import (
    REVIEW_ACTION,
    attempt_fact,
    audit_is_intact,
    identity,
    snapshot,
)

MAX_REVIEW_ROWS = 5000


@dataclass(frozen=True)
class ReviewedDelivery:
    id: str
    operation: str
    original_attempt_id: str = "unavailable"
    gateway_started_at: str = "unavailable"
    incident_ref: str = "unreviewed"


@dataclass(frozen=True)
class UnknownReviewStatus:
    acknowledged: tuple[ReviewedDelivery, ...]
    unreviewed: tuple[ReviewedDelivery, ...]
    invalidated: tuple[ReviewedDelivery, ...]
    unknown_total: int


async def classify_unknowns(session):
    audits = await _latest_audits(session)
    rows = await _load_relevant_rows(session, tuple(audits))
    attempts = await _load_attempts(session, tuple(row.id for row in rows.values()))
    return _classify(rows, attempts, audits)


async def _latest_audits(session):
    query = (
        select(AuditLog)
        .where(AuditLog.action == REVIEW_ACTION, AuditLog.aggregate_type == "outbox")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(MAX_REVIEW_ROWS + 1)
    )
    entries = tuple(await session.scalars(query))
    if len(entries) > MAX_REVIEW_ROWS:
        raise RuntimeError("too many delivery reviews to verify safely")
    latest = {}
    for audit in entries:
        latest.setdefault(UUID(audit.aggregate_id), audit)
    return latest


async def _load_relevant_rows(session, reviewed_ids):
    query = (
        select(TelegramOutbox)
        .where(
            or_(
                TelegramOutbox.state == OutboxState.UNKNOWN_HOLD,
                TelegramOutbox.id.in_(reviewed_ids),
            )
        )
        .limit(MAX_REVIEW_ROWS + 1)
    )
    rows = tuple(await session.scalars(query))
    if len(rows) > MAX_REVIEW_ROWS:
        raise RuntimeError("too many unresolved deliveries to verify safely")
    return {row.id: row for row in rows}


async def _load_attempts(session, ids):
    query = (
        select(DeliveryAttempt)
        .where(DeliveryAttempt.outbox_id.in_(ids))
        .order_by(DeliveryAttempt.outbox_id, DeliveryAttempt.attempt_number)
    )
    by_outbox = {row_id: [] for row_id in ids}
    for attempt in await session.scalars(query):
        by_outbox[attempt.outbox_id].append(attempt)
    return by_outbox


def _classify(rows, attempts, audits):
    groups = {"acknowledged": [], "unreviewed": [], "invalidated": []}
    for row_id in sorted(set(rows) | set(audits)):
        row = rows.get(row_id)
        review = audits.get(row_id)
        item = _delivery_item(row_id, row, review)
        category = _category(row, attempts.get(row_id, []), review)
        groups[category].append(item)
    total = sum(row.state is OutboxState.UNKNOWN_HOLD for row in rows.values())
    return UnknownReviewStatus(
        **{key: tuple(value) for key, value in groups.items()}, unknown_total=total
    )


def _delivery_item(row_id, row, review):
    details = review.details if review is not None and isinstance(review.details, dict) else {}
    started = row.gateway_started_at if row else None
    return ReviewedDelivery(
        str(row_id),
        row.operation if row else "missing",
        str(details.get("original_attempt_id") or "unavailable"),
        started.isoformat() if started else "unavailable",
        str(details.get("incident_ref") or "unreviewed"),
    )


def _category(row, attempts, review):
    if review is None:
        return "unreviewed"
    if row is None or not audit_is_intact(review):
        return "invalidated"
    if not _original_evidence_matches(row, attempts, review.details):
        return "invalidated"
    if row.state is OutboxState.UNKNOWN_HOLD:
        return (
            "acknowledged"
            if canonical_hash(snapshot(row, attempts)) == review.details.get("snapshot_hash")
            else "invalidated"
        )
    return "invalidated"


def _original_evidence_matches(row, attempts, review):
    if review.get("version") != 1 or review.get("delivery_result") != "unknown":
        return False
    if not review.get("incident_ref") or not review.get("manifest_sha256"):
        return False
    if canonical_hash(identity(row)) != review.get("identity_hash"):
        return False
    original = next((a for a in attempts if str(a.id) == review.get("original_attempt_id")), None)
    if original is None or original.certainty is not DeliveryCertainty.UNKNOWN:
        return False
    return canonical_hash(attempt_fact(original)) == review.get("original_attempt_hash")
