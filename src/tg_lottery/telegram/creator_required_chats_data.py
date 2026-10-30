from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_bonus_policy import current_bonus_policy
from tg_lottery.telegram.creator_group_selection import selected_chat_ids
from tg_lottery.telegram.required_chats import resolve_required_chat


def with_required_chats(data: dict[str, Any], chats: list[dict[str, Any]]) -> dict[str, Any]:
    chat_ids = [int(item["id"]) for item in chats]
    retained_ids = set(chat_ids) | set(selected_chat_ids(data))
    thresholds = {
        key: value
        for key, value in (data.get("chat_message_thresholds") or {}).items()
        if int(key) in retained_ids
    }
    return data | {
        "required_chats": chats,
        "required_chat_refs": chat_ids,
        "required_chat_ids": chat_ids,
        "chat_message_thresholds": thresholds,
    }


async def restore_required_chats(
    bot: object, data: dict[str, Any], *, user_id: int
) -> dict[str, Any]:
    records = list(data.get("required_chats") or [])
    refs = [record["id"] for record in records]
    refs = refs or data.get("required_chat_refs") or data.get("required_chat_ids") or []
    resolved = [await resolve_required_chat(bot, ref, admin_user_id=user_id) for ref in refs]
    return with_required_chats(data, resolved)


def validated_text_changes(text: str, *, key: str, label: str, limit: int) -> dict[str, str]:
    value = text.strip()
    if not 1 <= len(value) <= limit:
        raise UserFacingError(f"{label}长度必须为 1–{limit} 字")
    return {key: value}


async def required_draft_data(sessions: object, user_id: int) -> dict[str, Any]:
    session = await sessions.get(user_id)
    if session is None or not session.state.startswith("draft:"):
        raise UserFacingError("创建草稿已过期，请发送 /创建抽奖 重新创建")
    return current_bonus_policy(dict(session.data))
