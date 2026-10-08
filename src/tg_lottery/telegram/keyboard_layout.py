"""Compact semantic sections without changing button actions or token order."""

from collections.abc import Sequence
from unicodedata import east_asian_width

from aiogram.types import InlineKeyboardButton


def columns(items: Sequence) -> list[list]:
    return [list(items[index : index + 2]) for index in range(0, len(items), 2)]


def pair_rows(rows):
    return columns([button for row in rows for button in row])


def list_rows(rows, *, start: int = 1):
    buttons = [button for row in rows for button in row]
    result, pending = [], []
    for position, button in enumerate(buttons, start):
        label = button.text if isinstance(button, InlineKeyboardButton) else button[0]
        if label.startswith(("⚠", "❗")):
            result.extend(columns(pending))
            pending = []
            result.append([button])
            continue
        label = short_label(position, label)
        pending.append(
            button.model_copy(update={"text": label})
            if isinstance(button, InlineKeyboardButton)
            else (label, button[1])
        )
    return [*result, *columns(pending)]


def short_label(position: int, title: str) -> str:
    selected = ""
    for marker in ("✅", "⬜", "✓", "⚠️", "⚠"):
        if title.startswith(marker):
            selected, title = marker + " ", title[len(marker) :].lstrip()
            break
    units, visible = 0, []
    for char in title:
        units += 2 if east_asian_width(char) in {"W", "F"} else 1
        if units > 14:
            break
        visible.append(char)
    text = "".join(visible)
    return f"{selected}{position}·{text}" + ("…" if len(text) < len(title) else "")


def fit_rows(rows):
    """Split crowded presets/filters; retain the short minus/value/plus control."""
    result = []
    for row in rows:
        if len(row) <= 2 or _number_control(row):
            result.append(row)
        else:
            result.extend(columns(row))
    return result


def _number_control(row):
    if len(row) != 3:
        return False
    labels = [item.text if isinstance(item, InlineKeyboardButton) else item[0] for item in row]
    return ("−" in labels[0] or labels[0].startswith("-")) and labels[2].startswith(("＋", "+"))
