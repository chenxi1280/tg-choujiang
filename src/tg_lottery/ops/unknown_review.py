"""Preview or audit an explicitly approved set of unresolved delivery risks."""

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text

from tg_lottery.application.records import add_audit
from tg_lottery.config import get_settings
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import AuditLog, DeliveryAttempt, TelegramOutbox
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.ops.unknown_review_snapshot import (
    REVIEW_ACTION,
    ReviewRefused,
    audit_fact,
    audit_is_intact,
    review_details,
)


@dataclass(frozen=True)
class ReviewRequest:
    manifest: tuple[dict, ...]
    manifest_sha256: str
    incident_ref: str
    expected_count: int
    expected_fingerprint: str | None = None


def read_manifest(raw):
    rows = tuple(json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip())
    ids = [UUID(str(row["outbox_id"])) for row in rows]
    if not rows or len(set(ids)) != len(ids):
        raise ReviewRefused("manifest must contain distinct targets")
    return rows, sha256(raw).hexdigest()


def _validate_request(request):
    if len(request.manifest) != request.expected_count or request.expected_count > 500:
        raise ReviewRefused("manifest count does not match the approved count")
    ids = [UUID(str(item["outbox_id"])) for item in request.manifest]
    if not ids or len(set(ids)) != len(ids):
        raise ReviewRefused("manifest must contain distinct targets")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", request.incident_ref):
        raise ReviewRefused("invalid incident reference")
    if not re.fullmatch(r"[0-9a-f]{64}", request.manifest_sha256):
        raise ReviewRefused("invalid manifest hash")


def _validate_manifest_row(row, attempt, frozen):
    actual = {
        "outbox_id": str(row.id),
        "operation": row.operation,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "recipient_chat_id": row.recipient_chat_id,
        "state": row.state.value,
        "request_hash": row.request_hash,
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "attempt_certainty": attempt.certainty.value,
        "error_code": attempt.error_code,
        "error_class": "connector" if "ClientConnectorError" in row.last_error else "other_network",
    }
    if any(frozen.get(key) != value for key, value in actual.items()):
        raise ReviewRefused("target differs from the approved frozen manifest")
    for key in ("created_at", "updated_at", "gateway_started_at"):
        if datetime.fromisoformat(frozen[key]) != getattr(row, key):
            raise ReviewRefused("target timestamp changed since the approved manifest")
    if datetime.fromisoformat(frozen["attempt_finished_at"]) != attempt.finished_at:
        raise ReviewRefused("attempt timestamp changed since the approved manifest")


async def review_unknowns(sessions, request):
    _validate_request(request)
    frozen = {UUID(item["outbox_id"]): item for item in request.manifest}
    async with sessions() as session, session.begin():
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        await session.execute(text("SET LOCAL statement_timeout = '15s'"))
        rows, attempts = await _lock_targets(session, tuple(sorted(frozen)))
        entries = _review_entries(rows, attempts, request)
        report = _report(entries, request)
        if request.expected_fingerprint is not None:
            if request.expected_fingerprint != report["fingerprint"]:
                raise ReviewRefused("review preview changed")
            await _write_audits(session, entries, request)
        return report


async def _lock_targets(session, ids):
    rows = tuple(
        await session.scalars(
            select(TelegramOutbox)
            .where(TelegramOutbox.id.in_(ids))
            .order_by(TelegramOutbox.id)
            .with_for_update()
        )
    )
    attempts = tuple(
        await session.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.outbox_id.in_(ids))
            .order_by(DeliveryAttempt.outbox_id, DeliveryAttempt.attempt_number)
            .with_for_update()
        )
    )
    if len(rows) != len(ids):
        raise ReviewRefused("a frozen target is missing")
    return rows, attempts


def _review_entries(rows, attempts, request):
    frozen = {UUID(item["outbox_id"]): item for item in request.manifest}
    by_outbox = {row.id: [] for row in rows}
    for attempt in attempts:
        by_outbox[attempt.outbox_id].append(attempt)
    entries = []
    for row in rows:
        current = by_outbox[row.id]
        details = review_details(row, current, request.incident_ref)
        _validate_manifest_row(row, current[0], frozen[row.id])
        entries.append((row, details | {"manifest_sha256": request.manifest_sha256}))
    return entries


def _report(entries, request):
    facts = [{"outbox_id": str(row.id), **details} for row, details in entries]
    return {
        "count": len(facts),
        "fingerprint": canonical_hash({"entries": facts}),
        "applied": request.expected_fingerprint is not None,
        "items": facts,
    }


async def _write_audits(session, entries, request):
    ids = tuple(str(row.id) for row, _ in entries)
    existing = tuple(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.action == REVIEW_ACTION,
                AuditLog.aggregate_type == "outbox",
                AuditLog.aggregate_id.in_(ids),
            )
        )
    )
    created = []
    for entry in entries:
        row, details = entry
        if not _needs_audit(entry, existing, request.incident_ref):
            continue
        audit = add_audit(
            session,
            actor_user_id=None,
            action=REVIEW_ACTION,
            aggregate_type="outbox",
            aggregate_id=str(row.id),
            reason="Operator acknowledged unresolved historical delivery; no delivery is asserted",
            details=details,
        )
        audit.before_hash = details["snapshot_hash"]
        created.append(audit)
    await session.flush()
    for audit in created:
        audit.after_hash = canonical_hash(audit_fact(audit))


def _needs_audit(entry, existing, incident_ref):
    row, details = entry
    matching = [a for a in existing if a.aggregate_id == str(row.id)]
    same_incident = [a for a in matching if a.details.get("incident_ref") == incident_ref]
    if same_incident and any(a.details != details or not audit_is_intact(a) for a in same_incident):
        raise ReviewRefused("incident already has different review evidence")
    return not same_incident


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="JSONL file, or - for stdin")
    parser.add_argument("--incident-ref", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--expected-fingerprint")
    return parser.parse_args()


async def _run(args, raw):
    manifest, manifest_hash = read_manifest(raw)
    request = ReviewRequest(
        manifest, manifest_hash, args.incident_ref, args.expected_count, args.expected_fingerprint
    )
    engine = create_engine(get_settings().database_url)
    try:
        report = await review_unknowns(create_session_factory(engine), request)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        await engine.dispose()


def main():
    arguments = _parse_args()
    try:
        content = (
            sys.stdin.buffer.read()
            if arguments.manifest == "-"
            else Path(arguments.manifest).read_bytes()
        )
        asyncio.run(_run(arguments, content))
        return 0
    except ReviewRefused as exc:
        print(f"review_refused={exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError):
        print("review_refused=invalid_manifest_or_evidence", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"review_failed_type={type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
