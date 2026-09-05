from typing import Any

from aiogram.types import TelegramObject
from aiogram.utils.serialization import deserialize_telegram_object_to_python


def remote_object(value: TelegramObject | bool) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"ok": value}
    result = deserialize_telegram_object_to_python(value, include_api_method_name=False)
    if not isinstance(result, dict):
        raise TypeError("Telegram response must serialize to an object")
    return result
