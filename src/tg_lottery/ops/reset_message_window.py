"""Preview/apply an explicitly authorized live activity-only task-window correction."""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from tg_lottery.application.activity_rules import group_activity_ticket_target
from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.config import get_settings
from tg_lottery.domain.admission import validate_admission_window
from tg_lottery.domain.enums import CampaignState, OutboxState, TicketSource
from tg_lottery.domain.rules import CampaignRules, canonical_hash
from tg_lottery.infrastructure.db import (
    ActivityEvent,
    AdmissionProgress,
    AuditLog,
    Campaign,
    CampaignParticipationGroup,
    DailyProgress,
    Participant,
    RuleRevision,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.ops.message_window_data import (
    event_plan,
    snapshot_rows,
    verify_repair_rows,
    window_rows,
)


@dataclass(frozen=True, kw_only=True)
class WindowOptions:
    campaign_id: UUID
    expected_name: str
    expected_chat_id: int
    starts_at: datetime
    expected_cutoff: datetime
    expected_draw: datetime
    repair_ref: str
    expected_fingerprint: str | None = None


async def reset_message_window(sessions, options):
    async with sessions() as session, session.begin():
        await session.execute(text("SET LOCAL lock_timeout = '8s'"))
        await session.execute(text("SET LOCAL statement_timeout = '30s'"))
        campaign = await session.get(Campaign, options.campaign_id, with_for_update=True)
        _verify_identity(campaign, options)
        prior = await session.scalar(
            select(AuditLog).where(
                AuditLog.aggregate_id == str(campaign.id),
                AuditLog.action == "campaign.message_window_reset",
                AuditLog.reason == options.repair_ref,
            )
        )
        if prior:
            if campaign.preheat_at != options.starts_at or not campaign.rules.get(
                "message_task_calendar"
            ):
                raise ValueError("previous repair exists but active window changed")
            return {"status": "already_applied", "audit_id": str(prior.id)}
        rows = await window_rows(session, campaign)
        rules = _verify_scope(campaign, rows, options)
        changes, counts = await event_plan(
            session, campaign, starts_at=options.starts_at, rules=rules
        )
        report = _report(campaign, rows, options, changes=changes, counts=counts, rules=rules)
        if options.expected_fingerprint is None:
            return report
        if report["fingerprint"] != options.expected_fingerprint:
            raise ValueError("campaign configuration changed since preview")
        await _apply(
            session, campaign, rows, options=options, report=report, changes=changes, counts=counts
        )
        return {**report, "status": "applied"}


def _verify_identity(campaign, options):
    if not options.repair_ref.strip() or options.starts_at.tzinfo is None:
        raise ValueError("authorization reference and timezone-aware start are required")
    if campaign is None or campaign.name != options.expected_name:
        raise ValueError("campaign identity mismatch")
    if campaign.state != CampaignState.RUNNING:
        raise ValueError("only a running campaign may be repaired")
    if campaign.cutoff_at != options.expected_cutoff or campaign.draw_at != options.expected_draw:
        raise ValueError("cutoff or draw differs from authorized values")
    if options.starts_at < campaign.preheat_at:
        raise ValueError("repair cannot extend counting into earlier history")


def _verify_scope(campaign, rows, options):
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.admission is None or rules.admission.type != "message" or rules.invite_bonus_enabled:
        raise ValueError("repair requires message admission and no invitation bonuses")
    ids = {row.chat_id_snapshot for row in rows[CampaignParticipationGroup]}
    if ids != {options.expected_chat_id} or set(rules.admission.groups) != {
        str(options.expected_chat_id)
    }:
        raise ValueError("participation/gate group differs from authorized target")
    if (
        campaign.cutoff_at.astimezone(ZoneInfo(campaign.timezone)).date()
        <= utcnow().astimezone(ZoneInfo(campaign.timezone)).date()
    ):
        raise ValueError("repair must run before the final task day")
    revised = CampaignRules.model_validate(
        campaign.rules | {"message_task_calendar": "campaign-days-v1"}
    )
    validate_admission_window(revised, start_at=options.starts_at, cutoff_at=campaign.cutoff_at)
    verify_repair_rows(rows)
    if any(row.participation_group_id is None for row in rows[DailyProgress]):
        raise ValueError("legacy daily progress requires separate reconciliation")
    return revised


def _report(campaign, rows, options, *, changes, counts, rules):
    identity = {
        "campaign_id": str(campaign.id),
        "name": campaign.name,
        "old_start": campaign.preheat_at.isoformat(),
        "new_start": options.starts_at.isoformat(),
        "cutoff_at": campaign.cutoff_at.isoformat(),
        "draw_at": campaign.draw_at.isoformat(),
        "revision": campaign.config_revision,
        "rules_hash": canonical_hash(campaign.rules),
        "repair_ref": options.repair_ref,
        "chat_id": options.expected_chat_id,
    }
    deltas = _progress_deltas(rows, counts[ActivityEvent], rules)
    return {
        "status": "preview",
        "identity": identity,
        "fingerprint": canonical_hash(identity),
        "before": snapshot_rows(rows),
        "participants": len(rows[Participant]),
        "revoked_group_completions": sum(row.qualified for row in rows[AdmissionProgress]),
        "ticket_delta": sum(delta for _, _, delta in deltas),
        "progress_changes": [
            {"id": str(row.id), "new_messages": count, "ticket_delta": delta}
            for row, count, delta in deltas
        ],
        "event_corrections": {
            model.__tablename__: [
                {
                    "id": str(row.id),
                    "before_accepted": row.accepted,
                    "before_reason": row.rejection_reason,
                    "after_accepted": accepted,
                    "after_reason": reason,
                }
                for row, accepted, reason in items
            ]
            for model, items in changes.items()
        },
    }


def _progress_deltas(rows, counts, rules):
    chats = {row.id: row.chat_id_snapshot for row in rows[CampaignParticipationGroup]}
    result = []
    for row in rows[DailyProgress]:
        chat = chats[row.participation_group_id]
        count = counts[row.participant_id, chat, row.local_date]
        target = group_activity_ticket_target({str(chat): count}, chat, rules)
        if target is None:
            raise ValueError("daily progress has no enabled activity bonus rule")
        result.append((row, count, target - row.tickets_awarded))
    return result


async def _apply(session, campaign, rows, *, options, report, changes, counts):
    campaign.preheat_at = options.starts_at
    campaign.config_revision += 1
    campaign.rules = campaign.rules | {"message_task_calendar": "campaign-days-v1"}
    rules = CampaignRules.model_validate(campaign.rules)
    for items in changes.values():
        for event, accepted, reason in items:
            event.accepted, event.rejection_reason = accepted, reason
    participants = {row.id: row for row in rows[Participant]}
    for row, count, delta in _progress_deltas(rows, counts[ActivityEvent], rules):
        _correct_progress(session, campaign, row, options=options, count=count, delta=delta)
        participants[row.participant_id].ticket_total_cache += delta
    for participant in participants.values():
        participant.admission_qualified = False
    for admission in rows[AdmissionProgress]:
        admission.qualified = False
    for outbox in rows[TelegramOutbox]:
        outbox.state = OutboxState.CANCELLED
        outbox.last_error = (
            "Superseded by authorized task-window recalculation: " + options.repair_ref
        )
    _record_revision(session, campaign, report)
    await session.flush()


def _correct_progress(session, campaign, progress, *, options, count, delta):
    progress.valid_message_count = count
    progress.valid_message_counts = {str(options.expected_chat_id): count}
    progress.tickets_awarded += delta
    if not delta:
        return
    session.add(
        TicketLedger(
            campaign_id=campaign.id,
            participant_id=progress.participant_id,
            participation_group_id=progress.participation_group_id,
            delta=delta,
            source_type=TicketSource.POPULARITY,
            source_ref=str(progress.id),
            business_key=f"message_window_reset:{campaign.id}:{progress.id}:r{campaign.config_revision}",
            occurred_at=utcnow(),
        )
    )


def _record_revision(session, campaign, report):
    session.add(
        RuleRevision(
            campaign_id=campaign.id,
            revision=campaign.config_revision,
            rules=campaign.rules,
            rules_hash=canonical_hash(campaign.rules),
            frozen=True,
            actor_user_id=0,
        )
    )
    add_audit(
        session,
        actor_user_id=None,
        action="campaign.message_window_reset",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        reason=report["identity"]["repair_ref"],
        details=report,
    )


def _options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", type=UUID, required=True)
    parser.add_argument("--expected-name", required=True)
    parser.add_argument("--expected-chat-id", type=int, required=True)
    parser.add_argument("--starts-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--expected-cutoff", type=datetime.fromisoformat, required=True)
    parser.add_argument("--expected-draw", type=datetime.fromisoformat, required=True)
    parser.add_argument("--repair-ref", required=True)
    parser.add_argument("--expected-fingerprint")
    return WindowOptions(**vars(parser.parse_args()))


async def _run(options):
    engine = create_engine(get_settings().database_url)
    try:
        report = await reset_message_window(create_session_factory(engine), options)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run(_options()))
