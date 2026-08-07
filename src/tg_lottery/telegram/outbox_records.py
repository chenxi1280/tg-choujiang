from tg_lottery.infrastructure.db import TelegramOutbox


def delivery_log_context(outbox: TelegramOutbox, attempt_number: int) -> dict[str, object]:
    return {
        "outbox_id": str(outbox.id),
        "operation": outbox.operation,
        "attempt_number": attempt_number,
    }


def sanitized_remote_result(remote_result: dict[str, object]) -> dict[str, object]:
    sanitized = dict(remote_result)
    sanitized.pop("invite_link", None)
    return sanitized
