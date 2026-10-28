"""Bind an acknowledgement to the exact unresolved delivery facts."""

from hashlib import sha256

from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash

REVIEW_ACTION = "outbox.unknown_delivery_risk_acknowledged"
REVIEW_OPERATIONS = frozenset({"send_progress_notice", "send_temporary_reminder"})


class ReviewRefused(ValueError):
    """An approved review no longer matches its frozen evidence."""


def _time(value):
    return value.isoformat() if value is not None else None


def identity(row):
    return {
        "id": str(row.id),
        "operation": row.operation,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "recipient_chat_id": row.recipient_chat_id,
        "business_key": row.business_key,
        "request_hash": row.request_hash,
        "payload_hash": canonical_hash(row.payload),
        "created_at": _time(row.created_at),
    }


def attempt_fact(attempt):
    return {
        "id": str(attempt.id),
        "outbox_id": str(attempt.outbox_id),
        "number": attempt.attempt_number,
        "method": attempt.telegram_method,
        "certainty": attempt.certainty.value,
        "error_code": attempt.error_code,
        "error_hash": canonical_hash({"error": attempt.error_description}),
        "remote_result_hash": canonical_hash({"result": attempt.remote_result}),
        "created_at": _time(attempt.created_at),
        "finished_at": _time(attempt.finished_at),
    }


def audit_fact(audit):
    return {
        "id": str(audit.id),
        "actor_user_id": audit.actor_user_id,
        "action": audit.action,
        "aggregate_type": audit.aggregate_type,
        "aggregate_id": audit.aggregate_id,
        "reason": audit.reason,
        "details": audit.details,
        "source_update_id": audit.source_update_id,
        "created_at": _time(audit.created_at),
    }


def audit_is_intact(audit):
    if not isinstance(audit.details, dict):
        return False
    return audit.before_hash == audit.details.get(
        "snapshot_hash"
    ) and audit.after_hash == canonical_hash(audit_fact(audit))


def snapshot(row, attempts):
    secret = row.encrypted_remote_secret
    return {
        "identity": identity(row),
        "state": row.state.value,
        "run_at": _time(row.run_at),
        "updated_at": _time(row.updated_at),
        "gateway_started_at": _time(row.gateway_started_at),
        "lease_owner": row.lease_owner,
        "lease_until": _time(row.lease_until),
        "remote_message_id": row.remote_message_id,
        "remote_secret_hash": sha256(secret).hexdigest() if secret is not None else None,
        "projected_at": _time(row.projected_at),
        "last_error_hash": canonical_hash({"error": row.last_error}),
        "attempts": [attempt_fact(a) for a in sorted(attempts, key=lambda a: a.attempt_number)],
    }


def validate_reviewable(row, attempts):
    if row.operation not in REVIEW_OPERATIONS or row.state is not OutboxState.UNKNOWN_HOLD:
        raise ReviewRefused("target is not an eligible unknown notice")
    if canonical_hash(row.payload) != row.request_hash or len(attempts) != 1:
        raise ReviewRefused("request hash or original attempt count changed")
    _validate_original_attempt(row, attempts[0])
    _validate_no_remote_evidence(row, attempts[0])


def _validate_original_attempt(row, attempt):
    if (
        attempt.certainty is not DeliveryCertainty.UNKNOWN
        or attempt.telegram_method != row.operation
    ):
        raise ReviewRefused("original attempt is not the matching unknown operation")
    if attempt.error_code != "TelegramNetworkError" or attempt.error_description != row.last_error:
        raise ReviewRefused("original network error no longer matches")
    if row.gateway_started_at is None or attempt.finished_at < row.gateway_started_at:
        raise ReviewRefused("original gateway timing is invalid")


def _validate_no_remote_evidence(row, attempt):
    if any((row.lease_owner, row.lease_until, row.remote_message_id, row.encrypted_remote_secret)):
        raise ReviewRefused("target has a lease or remote result")
    if row.projected_at is not None or attempt.remote_result is not None:
        raise ReviewRefused("target already has remote evidence or a projection")


def review_details(row, attempts, incident_ref):
    validate_reviewable(row, attempts)
    return {
        "version": 1,
        "incident_ref": incident_ref,
        "operation": row.operation,
        "identity_hash": canonical_hash(identity(row)),
        "original_attempt_id": str(attempts[0].id),
        "original_attempt_hash": canonical_hash(attempt_fact(attempts[0])),
        "snapshot_hash": canonical_hash(snapshot(row, attempts)),
        "delivery_result": "unknown",
    }
