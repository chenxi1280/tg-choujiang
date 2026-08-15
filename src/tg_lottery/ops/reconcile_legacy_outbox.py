from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.config import get_settings
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.infrastructure.db import DeliveryAttempt, Group, TelegramOutbox
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory

LEGACY_ERROR_PREFIX = "unhandled errors in a TaskGroup"
TARGET_OPERATIONS = ("edit_campaign_card_state", "send_invite_link")


@dataclass(frozen=True)
class ReconcileWindow:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class Candidate:
    id: UUID
    operation: str
    aggregate_type: str
    aggregate_id: str
    recipient_title: str
    created_at: datetime
    updated_at: datetime
    request_hash: str
    payload_summary: str

    def digest_line(self) -> str:
        fields = (
            str(self.id),
            self.operation,
            self.aggregate_type,
            self.aggregate_id,
            self.created_at.isoformat(),
            self.updated_at.isoformat(),
            self.request_hash,
        )
        return "|".join(fields)


@dataclass(frozen=True)
class ReconcileReport:
    candidates: tuple[Candidate, ...]
    fingerprint: str
    applied: bool


@dataclass(frozen=True)
class ApplyOptions:
    window: ReconcileWindow
    expected_fingerprint: str
    expected_count: int
    repair_ref: str


async def preview(
    session_factory: async_sessionmaker[AsyncSession], window: ReconcileWindow
) -> ReconcileReport:
    async with session_factory() as session:
        rows = await _load_candidate_rows(session, window, lock=False)
        candidates = _candidates(rows)
        return ReconcileReport(candidates, _fingerprint(candidates), applied=False)


async def apply_reconciliation(
    session_factory: async_sessionmaker[AsyncSession], options: ApplyOptions
) -> ReconcileReport:
    async with session_factory() as session, session.begin():
        rows = await _load_candidate_rows(session, options.window, lock=True)
        candidates = _candidates(rows)
        _validate_apply(candidates, options)
        note = _repair_note(options.repair_ref)
        for row, _ in rows:
            row.state = OutboxState.CANCELLED
            row.last_error = note
            row.lease_owner = None
            row.lease_until = None
        return ReconcileReport(candidates, _fingerprint(candidates), applied=True)


async def _load_candidate_rows(
    session: AsyncSession, window: ReconcileWindow, *, lock: bool
) -> list[tuple[TelegramOutbox, str | None]]:
    query = (
        select(TelegramOutbox, Group.title)
        .outerjoin(Group, Group.chat_id == TelegramOutbox.recipient_chat_id)
        .where(
            TelegramOutbox.state == OutboxState.UNKNOWN_HOLD,
            TelegramOutbox.operation.in_(TARGET_OPERATIONS),
            TelegramOutbox.created_at >= window.start_at,
            TelegramOutbox.created_at <= window.end_at,
            TelegramOutbox.last_error.startswith(LEGACY_ERROR_PREFIX),
            _has_legacy_exception_attempt(),
        )
        .order_by(TelegramOutbox.created_at, TelegramOutbox.id)
    )
    if lock:
        query = query.with_for_update(of=TelegramOutbox)
    result = await session.execute(query)
    return list(result.all())


def _has_legacy_exception_attempt() -> object:
    return (
        select(DeliveryAttempt.id)
        .where(
            DeliveryAttempt.outbox_id == TelegramOutbox.id,
            DeliveryAttempt.certainty == DeliveryCertainty.UNKNOWN,
            DeliveryAttempt.error_code == "ExceptionGroup",
        )
        .exists()
    )


def _candidates(rows: list[tuple[TelegramOutbox, str | None]]) -> tuple[Candidate, ...]:
    return tuple(_candidate(row, title) for row, title in rows)


def _candidate(row: TelegramOutbox, recipient_title: str | None) -> Candidate:
    return Candidate(
        id=row.id,
        operation=row.operation,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        recipient_title=recipient_title or "(untracked chat)",
        created_at=row.created_at,
        updated_at=row.updated_at,
        request_hash=row.request_hash,
        payload_summary=_payload_summary(row),
    )


def _payload_summary(row: TelegramOutbox) -> str:
    payload = row.payload
    if row.operation == "edit_campaign_card_state":
        return f"message_id={payload.get('message_id')} has_cover={payload.get('has_cover')}"
    if row.operation == "send_invite_link":
        return f"invite_link_id={payload.get('invite_link_id')}"
    return "-"


def _fingerprint(candidates: tuple[Candidate, ...]) -> str:
    source = "\n".join(candidate.digest_line() for candidate in candidates)
    return sha256(source.encode()).hexdigest()


def _validate_apply(candidates: tuple[Candidate, ...], options: ApplyOptions) -> None:
    if len(candidates) != options.expected_count:
        raise RuntimeError(
            f"candidate count changed: expected {options.expected_count}, got {len(candidates)}"
        )
    fingerprint = _fingerprint(candidates)
    if fingerprint != options.expected_fingerprint:
        raise RuntimeError(
            "candidate fingerprint changed: "
            f"expected {options.expected_fingerprint}, got {fingerprint}"
        )


def _repair_note(repair_ref: str) -> str:
    now = datetime.now(UTC).isoformat()
    return (
        f"reconciled legacy ExceptionGroup unknown at {now}; ref={repair_ref}; "
        "original delivery_attempt certainty remains unknown for audit"
    )


def _parse_window(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return parsed.astimezone(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preview", "apply"), required=True)
    parser.add_argument("--start-at", type=_parse_window, required=True)
    parser.add_argument("--end-at", type=_parse_window, required=True)
    parser.add_argument("--expected-fingerprint", default="")
    parser.add_argument("--expected-count", type=int, default=3)
    parser.add_argument("--repair-ref", default="manual")
    return parser.parse_args()


async def _run(mode: Literal["preview", "apply"], args: argparse.Namespace) -> ReconcileReport:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    window = ReconcileWindow(args.start_at, args.end_at)
    try:
        if mode == "preview":
            return await preview(session_factory, window)
        options = ApplyOptions(
            window, args.expected_fingerprint, args.expected_count, args.repair_ref
        )
        return await apply_reconciliation(session_factory, options)
    finally:
        await engine.dispose()


def _print_report(report: ReconcileReport) -> None:
    print(f"applied={str(report.applied).lower()}")
    print(f"candidate_count={len(report.candidates)}")
    print(f"fingerprint={report.fingerprint}")
    for candidate in report.candidates:
        print(
            "candidate "
            f"id={candidate.id} operation={candidate.operation} "
            f"recipient={candidate.recipient_title} "
            f"created_at={candidate.created_at.isoformat()} payload={candidate.payload_summary}"
        )


def main() -> None:
    args = _parse_args()
    report = asyncio.run(_run(args.mode, args))
    _print_report(report)


if __name__ == "__main__":
    main()
