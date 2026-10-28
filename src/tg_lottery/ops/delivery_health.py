"""Report runtime delivery health, retaining visible acknowledged unknowns."""

import asyncio
import re
import sys
from dataclasses import dataclass

from sqlalchemy import func, select, text

from tg_lottery.config import get_settings
from tg_lottery.domain.enums import JobState, OutboxState, UpdateState
from tg_lottery.infrastructure.db import ScheduledJob, TelegramOutbox, TelegramUpdate
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.ops.delivery_health_review import UnknownReviewStatus, classify_unknowns
from tg_lottery.telegram.delivery_failures import SINGLE_NOTICE_OPERATIONS


@dataclass(frozen=True)
class DeliveryHealth:
    reviews: UnknownReviewStatus
    unprojected: int
    failed_jobs: int
    failed_updates: int
    rejected_ui: int
    unavailable_memberships: int
    failed_notices: int = 0


async def read_delivery_health(sessions):
    async with sessions() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout = '15s'"))
        reviews = await classify_unknowns(session)
        counts = await _health_counts(session)
        return DeliveryHealth(reviews, **counts)


async def _health_counts(session):
    queries = {
        "unprojected": select(func.count())
        .select_from(TelegramOutbox)
        .where(
            TelegramOutbox.state == OutboxState.CONFIRMED, TelegramOutbox.projected_at.is_(None)
        ),
        "failed_jobs": select(func.count())
        .select_from(ScheduledJob)
        .where(ScheduledJob.state.in_([JobState.BLOCKED, JobState.FAILED_TERMINAL])),
        "failed_updates": select(func.count())
        .select_from(TelegramUpdate)
        .where(TelegramUpdate.state == UpdateState.FAILED),
        "failed_notices": select(func.count())
        .select_from(TelegramOutbox)
        .where(
            TelegramOutbox.state == OutboxState.PRE_ACCEPT_FAILED,
            TelegramOutbox.operation.in_(SINGLE_NOTICE_OPERATIONS),
        ),
        "rejected_ui": _handled_update_count("UI response rejected:%"),
        "unavailable_memberships": _handled_update_count("Bot membership unavailable:%"),
    }
    return {name: int(await session.scalar(query)) for name, query in queries.items()}


def _handled_update_count(pattern):
    return (
        select(func.count())
        .select_from(TelegramUpdate)
        .where(
            TelegramUpdate.state == UpdateState.PROCESSED, TelegramUpdate.last_error.like(pattern)
        )
    )


def render_health(health):
    review = health.reviews
    failed = any(
        (
            review.unreviewed,
            review.invalidated,
            health.unprojected,
            health.failed_jobs,
            health.failed_updates,
            health.failed_notices,
        )
    )
    status = "DEGRADED" if failed else "DEGRADED_ACKNOWLEDGED" if review.acknowledged else "HEALTHY"
    lines = [
        f"STATUS={status} unknown_deliveries={review.unknown_total} "
        f"acknowledged_unknown={len(review.acknowledged)} "
        f"unreviewed_unknown={len(review.unreviewed)} "
        f"invalidated_reviews={len(review.invalidated)} "
        f"unprojected_confirmations={health.unprojected} failed_jobs={health.failed_jobs} "
        f"failed_updates={health.failed_updates} failed_notice_deliveries={health.failed_notices}",
        f"rejected_ui_responses={health.rejected_ui} "
        f"unavailable_memberships={health.unavailable_memberships}",
    ]
    lines.extend(_item_lines(review.acknowledged, "UNKNOWN_ACKNOWLEDGED"))
    lines.extend(_item_lines(review.unreviewed, "UNKNOWN_UNREVIEWED"))
    lines.extend(_item_lines(review.invalidated, "UNKNOWN_REVIEW_INVALIDATED"))
    if review.acknowledged:
        lines.append(
            "::warning title=Unconfirmed historical deliveries::"
            f"{len(review.acknowledged)} reviewed deliveries remain UNKNOWN; "
            "delivery and reminder deletion are not confirmed. See the summary for every ID."
        )
    if failed:
        lines.append(
            "::error title=Production health degraded::"
            "Unreviewed or changed delivery facts or failed work require attention."
        )
    return "\n".join(lines), 1 if failed else 0


def _item_lines(items, label):
    lines = []
    for item in items:
        operation = _safe(item.operation, r"[a-z0-9_]{1,64}")
        attempt = _safe(item.original_attempt_id, r"[a-z0-9-]{1,36}")
        started = _safe(item.gateway_started_at, r"[0-9TZ:+.a-z-]{1,40}")
        incident = _safe(item.incident_ref, r"[A-Za-z0-9._:-]{1,100}")
        lines.append(
            f"{label} outbox_id={item.id} operation={operation} "
            f"original_attempt_id={attempt} gateway_started_at={started} incident_ref={incident}"
        )
    return lines


def _safe(value, pattern):
    return value if re.fullmatch(pattern, value) else "invalid"


async def _run():
    engine = create_engine(get_settings().database_url)
    try:
        health = await asyncio.wait_for(read_delivery_health(create_session_factory(engine)), 30)
        output, status = render_health(health)
        print(output)
        return status
    finally:
        await engine.dispose()


def main():
    try:
        return asyncio.run(_run())
    except Exception as exc:
        print(f"STATUS=DEGRADED reason=delivery_health_error error_type={type(exc).__name__}")
        print("::error title=Production health unavailable::Delivery health could not be verified.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
