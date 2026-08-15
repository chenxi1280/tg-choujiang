"""有效发言判定规则: 门槛统计与每日加成共用同一口径。"""
import re
from typing import Any

_SPACE_RE = re.compile(r"\s+")
MIN_THRESHOLD_TEXT_LENGTH = 4  # >3 字


def normalize_text(text: str | None) -> str:
    return _SPACE_RE.sub(" ", (text or "").strip().casefold())


def rejection_reason(observation: Any, normalized: str, minimum_length: int) -> str | None:
    if observation.is_bot:
        return "bot"
    if observation.is_service:
        return "service"
    if observation.is_forwarded:
        return "forwarded"
    if normalized.startswith("/"):
        return "command"
    if len(normalized) < minimum_length:
        return "too_short"
    if not any(character.isalnum() for character in normalized):
        return "emoji_or_symbols_only"
    return None
