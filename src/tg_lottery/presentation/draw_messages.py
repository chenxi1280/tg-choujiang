import html
from typing import Any

from tg_lottery.presentation.mentions import MESSAGE_UTF16_LIMIT, html_utf16_length, user_mention


def draw_result_text(payload: dict[str, Any]) -> str:
    title = f"🏆 {html.escape(str(payload['campaign_name']))} 开奖结果"
    if payload.get("page_count", 1) > 1:
        title += f"（第 {payload['page_number']}/{payload['page_count']} 页）"
    lines = [title, *(_winner_line(item) for item in payload.get("winners", []))]
    if not payload.get("has_winners", bool(payload.get("winners"))):
        lines.append("本期没有符合条件的中奖候选人。")
    unallocated = payload.get("undistributed", [])
    if unallocated:
        lines.extend(["未分配名额:", *(_undistributed_line(item) for item in unallocated)])
    if payload.get("extended_tips"):
        lines.append("本次结果已保存，可在「我参加的抽奖」中查看。")
    return "\n".join(lines)


def _winner_line(item: dict[str, Any]) -> str:
    return (
        f"• 第{int(item['prize_position'])}顺位 {html.escape(str(item['prize_name']))}"
        f"（名额 {int(item['prize_slot_no'])}）: {user_mention(item)}"
    )


def _undistributed_line(item: dict[str, Any]) -> str:
    return (
        f"• 第{int(item['prize_position'])}顺位 {html.escape(str(item['prize_name']))}"
        f": {int(item['count'])} 人"
    )


def winner_notice_text(payload: dict[str, Any]) -> str:
    issuer = {
        "user_id": payload["issuer_user_id"],
        "username": payload.get("issuer_username"),
        "display_name": payload["issuer_name"],
    }
    return (
        f"恭喜你在「{html.escape(str(payload['campaign_name']))}」中获得: "
        f"{html.escape(str(payload['prize_name']))}（名额 {int(payload['prize_slot_no'])}）。\n"
        f"领奖方式: {html.escape(str(payload['claim_instructions']))}\n"
        f"发奖人员: {user_mention(issuer)}"
    )


def draw_result_pages(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if html_utf16_length(draw_result_text(payload)) <= MESSAGE_UTF16_LIMIT:
        return (payload,)
    items = tuple(
        (kind, item) for kind in ("winners", "undistributed") for item in payload.get(kind, [])
    )
    base = payload | {
        "winners": [],
        "undistributed": [],
        "has_winners": bool(payload.get("winners")),
        "page_number": len(items),
        "page_count": len(items),
    }
    pages, current = [], base
    for kind, item in items:
        candidate = current | {kind: [*current[kind], item]}
        if html_utf16_length(draw_result_text(candidate)) > MESSAGE_UTF16_LIMIT:
            if not current["winners"] and not current["undistributed"]:
                raise ValueError("a draw result row exceeds the Telegram message limit")
            pages.append(current)
            candidate = base | {kind: [item]}
            if html_utf16_length(draw_result_text(candidate)) > MESSAGE_UTF16_LIMIT:
                raise ValueError("a draw result row exceeds the Telegram message limit")
        current = candidate
    pages.append(current)
    return tuple(
        page | {"page_number": index, "page_count": len(pages)}
        for index, page in enumerate(pages, start=1)
    )
