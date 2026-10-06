"""有效发言判定规则: 门槛统计与每日加成共用同一口径。"""

import json
import re
from typing import Any

_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    return _SPACE_RE.sub(" ", (text or "").strip().casefold())


def normalized_content(observation: Any) -> str:
    text = normalize_text(observation.text)
    if observation.media_key is None:
        return text
    return json.dumps((observation.media_key, text), ensure_ascii=False)


def rejection_reason(observation: Any) -> str | None:
    if observation.is_bot:
        return "bot"
    if observation.is_service:
        return "service"
    if observation.is_forwarded:
        return "forwarded"
    normalized = normalize_text(observation.text)
    if normalized.startswith("/"):
        return "command"
    if not normalized and observation.media_key is None:
        return "empty"
    return None
