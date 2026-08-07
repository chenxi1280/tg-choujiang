from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import JobState, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import AuditLog, ScheduledJob, TelegramOutbox


def utcnow() -> datetime:
    return datetime.now(UTC)


def add_job(
    session: Any,
    *,
    job_type: str,
    aggregate_id: str,
    business_key: str,
    run_at: datetime,
    payload: dict[str, Any] | None = None,
) -> ScheduledJob:
    job = ScheduledJob(
        job_type=job_type,
        aggregate_id=aggregate_id,
        business_key=business_key,
        run_at=run_at,
        payload=payload or {},
        state=JobState.PENDING,
    )
    session.add(job)
    return job


def add_outbox(
    session: Any,
    *,
    operation: str,
    aggregate_type: str,
    aggregate_id: str,
    recipient_chat_id: int,
    business_key: str,
    payload: dict[str, Any],
) -> TelegramOutbox:
    outbox = TelegramOutbox(
        operation=operation,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        recipient_chat_id=recipient_chat_id,
        business_key=business_key,
        request_hash=canonical_hash(payload),
        payload=payload,
        state=OutboxState.PENDING,
        run_at=utcnow(),
    )
    session.add(outbox)
    return outbox


def add_audit(
    session: Any,
    *,
    actor_user_id: int | None,
    action: str,
    aggregate_type: str,
    aggregate_id: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    audit = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        reason=reason,
        details=details or {},
        source_update_id=current_update_id(),
    )
    session.add(audit)
    return audit
