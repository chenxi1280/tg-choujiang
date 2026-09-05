import html
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any

MESSAGE_UTF16_LIMIT = 4096
CAPTION_UTF16_LIMIT = 1024


def user_label(user: Mapping[str, Any]) -> str:
    username = user.get("username")
    return f"@{username}" if username else f"@{user['display_name']}"


def user_mention(user: Mapping[str, Any]) -> str:
    user_id = int(user["user_id"])
    if user_id <= 0:
        raise ValueError("mentions require a positive Telegram user ID")
    # Bind the mention to the winning identity even if their username subsequently changes.
    return f'<a href="tg://user?id={user_id}">{html.escape(user_label(user))}</a>'


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_utf16_length(text: str) -> int:
    parser = _PlainText()
    parser.feed(text)
    parser.close()
    return len("".join(parser.parts).encode("utf-16-le")) // 2
