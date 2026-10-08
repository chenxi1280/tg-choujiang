import html
from typing import Any

from tg_lottery.presentation.mentions import (
    CAPTION_UTF16_LIMIT,
    MESSAGE_UTF16_LIMIT,
    html_utf16_length,
    user_mention,
)

_CARD_WINNER_LIMIT = 8


def campaign_state_card_text(payload: dict[str, Any]) -> str:
    state = str(payload["card_state"])
    status = {
        "settling": "报名已截止，正在复核资格并结算抽奖次数。",
        "drawn": "本期已经开奖，报名入口已关闭，请查看最新开奖结果。",
    }.get(state)
    if status is None:
        raise ValueError("unsupported campaign card state")
    name = html.escape(str(payload["campaign_name"]))
    text = f"✨ <b>{name}</b>\n\n{status}"
    if payload.get("frozen_rules_text"):
        text = status + "\n\n" + payload["frozen_rules_text"]
    return _with_winner_summary(text, payload)


def _with_winner_summary(text: str, payload: dict[str, Any]) -> str:
    winners = payload.get("winners") or []
    if not winners:
        return text
    prefix = text + "\n\n🏆 中奖者：\n"
    footer = f"共 {len(winners)} 个中奖名额（完整名单见群内结果公告）"
    limit = CAPTION_UTF16_LIMIT if payload.get("has_cover") else MESSAGE_UTF16_LIMIT
    rows: list[str] = []
    for winner in winners[:_CARD_WINNER_LIMIT]:
        candidate = [*rows, f"• {user_mention(winner)}"]
        if html_utf16_length(prefix + "\n".join([*candidate, footer])) > limit:
            break
        rows = candidate
    if len(rows) < len(winners):
        rows.append(footer)
    return prefix + "\n".join(rows)
