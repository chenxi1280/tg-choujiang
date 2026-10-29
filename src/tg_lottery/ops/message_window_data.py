"""Build a deterministic repair plan without deleting source messages or ledger entries."""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import select

from tg_lottery.domain.enums import OutboxState, TicketSource
from tg_lottery.infrastructure.db import (
    ActivityEvent,
    AdmissionProgress,
    BaseActivityEvent,
    CampaignParticipationGroup,
    DailyProgress,
    DrawSnapshot,
    Participant,
    TelegramOutbox,
    TicketLedger,
)


async def window_rows(session, campaign):
    if await session.scalar(select(DrawSnapshot.id).where(DrawSnapshot.campaign_id == campaign.id)):
        raise ValueError("campaign already has an immutable draw snapshot")
    models = (
        Participant,
        DailyProgress,
        AdmissionProgress,
        TicketLedger,
        CampaignParticipationGroup,
    )
    rows = {}
    for model in models:
        rows[model] = list(
            await session.scalars(
                select(model)
                .where(model.campaign_id == campaign.id)
                .order_by(model.id)
                .with_for_update()
            )
        )
    ids = [row.id for row in rows[Participant]]
    rows[TelegramOutbox] = list(
        await session.scalars(
            select(TelegramOutbox)
            .where(
                TelegramOutbox.aggregate_type == "participant",
                TelegramOutbox.aggregate_id.in_(list(map(str, ids))),
                TelegramOutbox.operation == "send_progress_notice",
                TelegramOutbox.state.in_(
                    [
                        OutboxState.PENDING,
                        OutboxState.RETRY_SCHEDULED,
                        OutboxState.CLAIMED,
                    ]
                ),
            )
            .order_by(TelegramOutbox.id)
            .with_for_update()
        )
    )
    return rows


async def event_plan(session, campaign, *, starts_at, rules):
    plans, counts = {}, {}
    for model in (BaseActivityEvent, ActivityEvent):
        events = list(
            await session.scalars(
                select(model)
                .where(
                    model.campaign_id == campaign.id,
                    model.event_at <= campaign.cutoff_at,
                )
                .order_by(model.event_at, model.source_update_id, model.message_id)
            )
        )
        plans[model], counts[model] = recount_events(
            events, minutes=rules.activity.duplicate_window_minutes, starts_at=starts_at
        )
    return plans, counts


def recount_events(events, *, minutes, starts_at):
    changes, counts, seen = [], Counter(), {}
    for event in events:
        if event.event_at < starts_at:
            if event.accepted:
                changes.append((event, False, "before_task_window"))
            continue
        if event.rejection_reason not in (None, "duplicate_text_window"):
            continue
        key = (event.participant_id, event.chat_id, event.content_hash)
        prior = seen.get(key)
        accepted = prior is None or event.event_at - prior > timedelta(minutes=minutes)
        if accepted:
            seen[key] = event.event_at
            counts[event.participant_id, event.chat_id, event.local_date] += 1
        reason = None if accepted else "duplicate_text_window"
        if event.accepted != accepted or event.rejection_reason != reason:
            changes.append((event, accepted, reason))
    return changes, counts


def snapshot_rows(rows):
    fields = {
        Participant: ("id", "admission_qualified", "ticket_total_cache", "counting_started_at"),
        DailyProgress: (
            "id",
            "participant_id",
            "participation_group_id",
            "local_date",
            "valid_message_count",
            "valid_message_counts",
            "tickets_awarded",
        ),
        AdmissionProgress: (
            "id",
            "participant_id",
            "qualified",
            "completed_at",
            "source_ref",
            "config_revision",
        ),
        TicketLedger: ("id", "participant_id", "delta", "source_type", "source_ref"),
        TelegramOutbox: ("id", "state", "request_hash"),
    }
    return {
        model.__tablename__: [
            {key: _json(getattr(row, key)) for key in keys} for row in rows[model]
        ]
        for model, keys in fields.items()
    }


def verify_repair_rows(rows):
    _verify_ledger_caches(rows)
    for row in rows[TelegramOutbox]:
        if row.state == OutboxState.CLAIMED or row.lease_owner or row.gateway_started_at:
            raise ValueError("progress delivery is in flight; retry after executor finishes")


def _verify_ledger_caches(rows):
    totals = defaultdict(int)
    progress_totals = defaultdict(int)
    for entry in rows[TicketLedger]:
        if entry.source_type not in (TicketSource.REGISTRATION, TicketSource.POPULARITY):
            raise ValueError("repair requires an activity-only ledger without invitation reversals")
        totals[entry.participant_id] += entry.delta
        if entry.source_type == TicketSource.POPULARITY:
            progress_totals[entry.source_ref] += entry.delta
    for progress in rows[DailyProgress]:
        if progress.tickets_awarded != progress_totals[str(progress.id)]:
            raise ValueError("daily awarded cache does not match existing ledger")
    for participant in rows[Participant]:
        if participant.ticket_total_cache != totals[participant.id]:
            raise ValueError("participant cache does not match existing ledger")


def _json(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if type(value).__name__ == "UUID" else value
