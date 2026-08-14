from datetime import UTC, datetime

from aiogram.types import Update

from tg_lottery.telegram.polling import _serialize_update


def test_polling_serializes_updates_with_link_preview_defaults() -> None:
    timestamp = int(datetime.now(UTC).timestamp())
    update = Update.model_validate(
        {
            "update_id": 11,
            "message": {
                "message_id": 11,
                "date": timestamp,
                "chat": {"id": 201, "type": "private"},
                "from": {"id": 201, "is_bot": False, "first_name": "Tester"},
                "text": "https://t.me/example",
                "link_preview_options": {"url": "https://t.me/example"},
            },
        }
    )

    payload = _serialize_update(update)

    assert payload["message"]["link_preview_options"] == {"url": "https://t.me/example"}
