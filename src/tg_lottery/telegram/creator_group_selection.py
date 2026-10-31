from __future__ import annotations

from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Group
from tg_lottery.telegram.creator_editor_cleanup import retain_group_editors
from tg_lottery.telegram.required_chats import resolve_required_chat


async def resolve_participation_groups(
    bot: object,
    *,
    chat_ids: tuple[int, ...],
    admin_user_id: int,
    activity_enabled: bool,
    activity_chat_ids: frozenset[int] | None = None,
) -> list[dict[str, Any]]:
    records = []
    errors = []
    for position, chat_id in enumerate(chat_ids, start=1):
        try:
            record = await resolve_required_chat(bot, chat_id, admin_user_id=admin_user_id)
            if record.get("type") not in {"group", "supergroup"}:
                raise UserFacingError(f"参与目标「{record['title']}」不是群聊")
            enabled = (
                activity_enabled if activity_chat_ids is None else chat_id in activity_chat_ids
            )
            validate_activity_target(record, activity_enabled=enabled)
            records.append(record | {"position": position})
        except UserFacingError as exc:
            errors.append(f"· 第 {position} 个参与群：{exc.public_message}")
    if errors:
        raise UserFacingError("以下参与群需要处理：\n" + "\n".join(errors))
    return records


def configured_activity_chat_ids(data: dict[str, Any]) -> frozenset[int] | None:
    base = set(map(int, data.get("base_activity_rules") or {}))
    gate = data.get("admission") or {}
    if gate.get("type") == "message":
        base.update(map(int, gate.get("groups") or {}))
    configured = _active_activity_rules(data)
    if configured or base:
        active = {int(chat_id) for chat_id, rule in configured.items() if rule.get("enabled")}
        return frozenset(active | base)
    return None


def _active_activity_rules(data: dict) -> dict:
    if data.get("participation_rules_version") == "v3.0" and "activity" not in data.get(
        "bonus_types", []
    ):
        return {}
    return data.get("group_activity_rules") or {}


def validate_activity_target(record: dict[str, Any], *, activity_enabled: bool) -> None:
    if not activity_enabled or record.get("type") == "channel":
        return
    allowed = record.get("member_can_send_messages")
    if allowed is True:
        return
    title = record["title"]
    if allowed is False:
        raise UserFacingError(f"强制群「{title}」禁止普通成员发言，无法启用抽奖加成票(发言)")
    raise UserFacingError(f"无法确认强制群「{title}」的普通成员发言权限，请检查群权限后重试")


async def resolve_requirements(
    bot: object, data: dict[str, Any], *, creator_user_id: int
) -> list[dict[str, Any]]:
    refs = data.get("required_chat_refs") or data.get("required_chat_ids", [])
    records: list[dict[str, Any]] = []
    seen = set(selected_chat_ids(data))
    activity_enabled = "activity" in data.get("bonus_types", [])
    activity_chat_ids = configured_activity_chat_ids(data)
    errors = []
    for position, ref in enumerate(refs, start=1):
        try:
            record = await resolve_required_chat(bot, ref, admin_user_id=creator_user_id)
            chat_id = int(record["id"])
            if chat_id in seen:
                raise UserFacingError(
                    f"目标「{record['title']}」同时被选为参与群和准入条件，请只保留一种用途"
                )
            enabled = activity_enabled and (
                activity_chat_ids is None or chat_id in activity_chat_ids
            )
            validate_activity_target(record, activity_enabled=enabled)
            seen.add(chat_id)
            records.append(record | {"position": len(records) + 1})
        except UserFacingError as exc:
            errors.append(f"· 第 {position} 个准入条件：{exc.public_message}")
    if errors:
        raise UserFacingError("以下准入条件需要处理：\n" + "\n".join(errors))
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
    selected = {str(item["id"]) for item in records}
    invite_rules = {
        key: value
        for key, value in (data.get("group_invite_rules") or {}).items()
        if key in selected
    }
    activity_rules = {
        key: value
        for key, value in (data.get("group_activity_rules") or {}).items()
        if key in selected
    }
    return retain_group_editors(
        data
        | {
            "chat_id": int(primary["id"]),
            "group_title": str(primary["title"]),
            "timezone": str(primary["timezone"]),
            "participation_group_ids": [int(item["id"]) for item in records],
            "participation_groups": records,
            "group_invite_rules": invite_rules,
            "base_activity_rules": {
                k: v for k, v in (data.get("base_activity_rules") or {}).items() if k in selected
            },
            "group_activity_rules": activity_rules,
            "admission": _retained_admission(data, selected),
        },
        selected,
    )


def _clear_groups(data: dict[str, Any]) -> dict[str, Any]:
    return retain_group_editors(
        data
        | {
            "chat_id": None,
            "group_title": "",
            "participation_group_ids": [],
            "participation_groups": [],
            "group_invite_rules": {},
            "group_activity_rules": {},
            "base_activity_rules": {},
            "admission": None,
        },
        set(),
    )


def _group_record(group: Group) -> dict[str, Any]:
    return {
        "id": group.chat_id,
        "group_id": str(group.id),
        "title": group.title,
        "type": "group",
        "timezone": group.timezone,
    }


def _retained_admission(data: dict, selected: set) -> dict | None:
    gate = data.get("admission")
    if not gate:
        return None
    return gate | {
        "groups": {key: value for key, value in gate["groups"].items() if key in selected}
    }
