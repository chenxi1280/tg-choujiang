from __future__ import annotations

from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Group
from tg_lottery.telegram.required_chats import resolve_required_chat


async def resolve_participation_groups(
    bot: object,
    *,
    chat_ids: tuple[int, ...],
    admin_user_id: int,
    activity_enabled: bool,
) -> list[dict[str, Any]]:
    records = []
    for position, chat_id in enumerate(chat_ids, start=1):
        record = await resolve_required_chat(bot, chat_id, admin_user_id=admin_user_id)
        if record.get("type") not in {"group", "supergroup"}:
            raise UserFacingError(f"参与目标「{record['title']}」不是群聊")
        validate_activity_target(record, activity_enabled=activity_enabled)
        records.append(record | {"position": position})
    return records


def validate_activity_target(record: dict[str, Any], *, activity_enabled: bool) -> None:
    if not activity_enabled or record.get("type") == "channel":
        return
    allowed = record.get("member_can_send_messages")
    if allowed is True:
        return
    title = record["title"]
    if allowed is False:
        raise UserFacingError(f"强制群「{title}」禁止普通成员发言，无法启用发言加成")
    raise UserFacingError(f"无法确认强制群「{title}」的普通成员发言权限，请检查群权限后重试")


async def resolve_requirements(
    bot: object, data: dict[str, Any], *, creator_user_id: int
) -> list[dict[str, Any]]:
    refs = data.get("required_chat_refs") or data.get("required_chat_ids", [])
    records: list[dict[str, Any]] = []
    seen = set(selected_chat_ids(data))
    activity_enabled = "activity" in data.get("bonus_types", [])
    for ref in refs:
        record = await resolve_required_chat(bot, ref, admin_user_id=creator_user_id)
        chat_id = int(record["id"])
        if chat_id in seen:
            raise UserFacingError(
                f"目标「{record['title']}」同时被选为参与群和准入条件，请只保留一种用途"
            )
        validate_activity_target(record, activity_enabled=activity_enabled)
        seen.add(chat_id)
        records.append(record | {"position": len(records) + 1})
    return records


def bind_initial_group(data: dict[str, Any], group: Group) -> dict[str, Any]:
    record = _group_record(group)
    return data | {
        "chat_id": group.chat_id,
        "group_title": group.title,
        "timezone": group.timezone,
        "participation_group_ids": [group.chat_id],
        "participation_groups": [record],
    }


def toggle_group(data: dict[str, Any], group: Group) -> dict[str, Any]:
    records = list(data.get("participation_groups") or [])
    selected = {int(item["id"]) for item in records}
    if group.chat_id in selected:
        records = [item for item in records if int(item["id"]) != group.chat_id]
    else:
        records.append(_group_record(group))
    if not records:
        return _clear_groups(data)
    current_primary = int(data["chat_id"]) if data.get("chat_id") is not None else None
    primary = next((item for item in records if int(item["id"]) == current_primary), records[0])
    return _with_groups(data, records, primary)


def set_primary_group(data: dict[str, Any], group: Group) -> dict[str, Any]:
    records = list(data.get("participation_groups") or [])
    if group.chat_id not in {int(item["id"]) for item in records}:
        raise UserFacingError("请先勾选该群，再将它设为主参与群")
    primary = next(item for item in records if int(item["id"]) == group.chat_id)
    ordered = [primary, *(item for item in records if item is not primary)]
    return _with_groups(data, ordered, primary)


def selected_chat_ids(data: dict[str, Any]) -> tuple[int, ...]:
    records = data.get("participation_groups") or []
    if records:
        return tuple(int(item["id"]) for item in records)
    configured = data.get("participation_group_ids") or []
    if configured:
        return tuple(int(value) for value in configured)
    return (int(data["chat_id"]),) if data.get("chat_id") is not None else ()


def _with_groups(
    data: dict[str, Any], records: list[dict[str, Any]], primary: dict[str, Any]
) -> dict[str, Any]:
    return data | {
        "chat_id": int(primary["id"]),
        "group_title": str(primary["title"]),
        "timezone": str(primary["timezone"]),
        "participation_group_ids": [int(item["id"]) for item in records],
        "participation_groups": records,
    }


def _clear_groups(data: dict[str, Any]) -> dict[str, Any]:
    return data | {
        "chat_id": None,
        "group_title": "",
        "participation_group_ids": [],
        "participation_groups": [],
    }


def _group_record(group: Group) -> dict[str, Any]:
    return {
        "id": group.chat_id,
        "title": group.title,
        "type": "group",
        "timezone": group.timezone,
    }
