"""Recover only retained, processed private Telegram facts; never infer from reachability."""

import re

from sqlalchemy import or_, select

from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import BotRelationship, TelegramUpdate


async def recover_relationship(session, bot_id, *, user_id):
    rows = tuple(
        await session.scalars(
            select(TelegramUpdate)
            .where(
                TelegramUpdate.bot_id == bot_id,
                TelegramUpdate.routing_chat_id == user_id,
                TelegramUpdate.state == UpdateState.PROCESSED,
                or_(
                    TelegramUpdate.update_type == "my_chat_member",
                    TelegramUpdate.payload["message"]["text"]
                    .as_string()
                    .op("~")(r"^/start(?:\s|$)"),
                ),
            )
            .order_by(TelegramUpdate.event_at.desc(), TelegramUpdate.id.desc())
            .limit(200)
        )
    )
    facts = [(row, _fact(row, bot_id=bot_id, user_id=user_id)) for row in reversed(rows)]
    if not any(kind == "start" for _, kind in facts):
        return None
    from tg_lottery.application.bot_relationships import _apply_observation

    record = BotRelationship(bot_id=bot_id, user_id=user_id, conflicted=False)
    for row, kind in facts:
        if kind is None:
            continue
        if kind == "start" and record.started_at is None:
            record.started_at, record.start_update_id = row.event_at, row.update_id
        _apply_observation(record, row.event_at, update_id=row.update_id, blocked=kind == "blocked")
    session.add(record)
    return record


def _fact(row, *, bot_id, user_id):
    payload = row.payload or {}
    message = payload.get("message")
    if message is not None:
        return "start" if _own_start(message, user_id=user_id) else None
    return _member_fact(payload.get("my_chat_member") or {}, bot_id=bot_id, user_id=user_id)


def _member_fact(event, *, bot_id, user_id):
    member = event.get("new_chat_member") or {}
    if event.get("chat", {}).get("type") != "private" or event.get("chat", {}).get("id") != user_id:
        return None
    if member.get("user", {}).get("id") != bot_id:
        return None
    if member.get("status") in {"kicked", "left"}:
        return "blocked"
    return "unblocked" if member.get("status") == "member" else None


def _own_start(message, *, user_id):
    chat, sender = message.get("chat") or {}, message.get("from") or {}
    return (
        chat.get("type") == "private"
        and chat.get("id") == user_id
        and sender.get("id") == user_id
        and sender.get("is_bot") is False
        and not message.get("forward_origin")
        and re.match(r"^/start(?:\s|$)", message.get("text") or "") is not None
    )


def projected_start_exists():
    return (
        select(BotRelationship.user_id)
        .where(
            BotRelationship.bot_id == TelegramUpdate.bot_id,
            BotRelationship.user_id == TelegramUpdate.routing_chat_id,
            BotRelationship.started_at.is_not(None),
            or_(
                BotRelationship.start_update_id == TelegramUpdate.update_id,
                BotRelationship.observed_update_id == TelegramUpdate.update_id,
            ),
            TelegramUpdate.update_type == "message",
            TelegramUpdate.payload["message"]["text"]
            .as_string()
            .op("~")(r"^/start(?:\s+(?:join|rules|bonus)_[A-Za-z0-9_-]+)?\s*$"),
        )
        .correlate(TelegramUpdate)
        .exists()
    )
