from __future__ import annotations

from typing import Any

from aiogram import Bot

from tg_lottery.domain.errors import AuthorizationDenied, UserFacingError
from tg_lottery.telegram.admin_authorization import verify_chat_admin
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_group_selection import validate_activity_target
from tg_lottery.telegram.required_chats import resolve_required_chat

COPY_PAGE_SIZE = 6


async def verify_bonus_action(bot: Bot, user_id: int, data: dict[str, Any], *, action: str) -> None:
    targets = _action_targets(data, action)
    if not targets:
        return
    targets.extend((int(key), "activity") for key in data.get("base_activity_rules", {}))
    errors = await _permission_errors(bot, user_id, targets, data=data)
    if errors:
        raise UserFacingError("以下群的加成权限需要修复：\n" + "\n".join(errors))


async def verify_publication_bonus_permissions(
    bot: Bot, user_id: int, data: dict[str, Any]
) -> None:
    targets = [
        (int(chat_id), "invite")
        for chat_id, rule in (data.get("group_invite_rules") or {}).items()
        if rule.get("enabled")
    ]
    targets.extend(
        (int(chat_id), "activity")
        for chat_id, rule in (data.get("group_activity_rules") or {}).items()
        if rule.get("enabled")
    )
    targets.extend((int(key), "activity") for key in data.get("base_activity_rules", {}))
    errors = await _permission_errors(bot, user_id, targets, data=data)
    if errors:
        raise UserFacingError("以下群的加成权限需要修复：\n" + "\n".join(errors))


def _action_targets(data: dict[str, Any], action: str) -> list[tuple[int, str]]:
    if action.endswith("_save"):
        return _working_target(data)
    if action.startswith("draft_bonus_copy_toggle:"):
        return _copy_toggle_target(data, action)
    if action == "draft_bonus_copy_all":
        return _copy_page_targets(data)
    if action == "draft_bonus_copy_confirm":
        return _copy_confirm_targets(data)
    return []


def _working_target(data: dict[str, Any]) -> list[tuple[int, str]]:
    working = data.get("bonus_working_copy") or {}
    if not working:
        return []
    return [(int(working["chat_id"]), str(working["kind"]))]


def _copy_toggle_target(data: dict[str, Any], action: str) -> list[tuple[int, str]]:
    copy_state = data.get("bonus_copy") or {}
    if not copy_state:
        return []
    chat_id = int(action.rsplit(":", 1)[-1])
    return [(chat_id, str(copy_state["kind"]))]


def _copy_page_targets(data: dict[str, Any]) -> list[tuple[int, str]]:
    copy_state = data.get("bonus_copy") or {}
    if not copy_state:
        return []
    source_id = int(copy_state["source_chat_id"])
    candidates = [
        int(group["id"])
        for group in data.get("participation_groups") or []
        if int(group["id"]) != source_id
    ]
    page = int(copy_state.get("page", 0))
    page_ids = candidates[page * COPY_PAGE_SIZE : (page + 1) * COPY_PAGE_SIZE]
    return [(chat_id, str(copy_state["kind"])) for chat_id in page_ids]


def _copy_confirm_targets(data: dict[str, Any]) -> list[tuple[int, str]]:
    copy_state = data.get("bonus_copy") or {}
    if not copy_state:
        return []
    kind = str(copy_state["kind"])
    return [(int(chat_id), kind) for chat_id in copy_state.get("targets", [])]


async def _permission_errors(
    bot: Bot, user_id: int, targets: list[tuple[int, str]], *, data: dict[str, Any]
) -> list[str]:
    errors = []
    names = {
        int(group["id"]): chat_title(group) for group in data.get("participation_groups") or []
    }
    for position, (chat_id, kind) in enumerate(dict.fromkeys(targets), start=1):
        try:
            await _verify_target(bot, user_id, chat_id, kind=kind)
        except (AuthorizationDenied, UserFacingError) as exc:
            name = names.get(chat_id, f"第 {position} 个参与群")
            errors.append(f"· {name}：{exc}")
    return errors


async def _verify_target(bot: Bot, user_id: int, chat_id: int, *, kind: str) -> None:
    if kind == "invite":
        await verify_chat_admin(bot, chat_id, user_id, require_invites=True)
        return
    record = await resolve_required_chat(bot, chat_id, admin_user_id=user_id)
    validate_activity_target(record, activity_enabled=True)
