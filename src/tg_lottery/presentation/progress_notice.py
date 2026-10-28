"""Render safe Telegram links without changing the persisted progress text."""

import html
from urllib.parse import urlsplit

from tg_lottery.presentation.campaign_card import campaign_link


def progress_notice_html(payload, *, bot_username):
    text = str(payload["text"])
    parts, cursor = [], 0
    for span in payload.get("group_links") or []:
        start = span["offset"]
        end = start + span["length"]
        url = _span_url(span, payload=payload, bot_username=bot_username)
        if start < cursor or end > len(text) or end <= start or not url:
            continue
        parts.append(html.escape(text[cursor:start]))
        parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(text[start:end])}</a>')
        cursor = end
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def _span_url(span, *, payload, bot_username):
    url = span.get("url")
    position = span.get("condition_position")
    if not url and bot_username and isinstance(position, int) and position > 0:
        url = campaign_link(payload | {"bot_username": bot_username}, "condition") + f"_{position}"
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "t.me" or not parsed.path.strip("/"):
        return None
    return url
