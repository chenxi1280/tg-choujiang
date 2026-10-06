from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tg_lottery.domain.rules import CampaignRules


async def inspect_bonus_permissions(
    bot: Bot, rules: CampaignRules, *, targets: tuple = ()
) -> dict[int, str]:
    errors = {}
    current_ids = {(target.rule_chat_id or target.chat_id): target.chat_id for target in targets}
    for chat_id, group_kinds in _permission_targets(rules).items():
        reason = await _permission_error(bot, current_ids.get(chat_id, chat_id), group_kinds)
        if reason:
            errors[chat_id] = reason
    return errors


def _permission_targets(rules: CampaignRules) -> dict[int, set[str]]:
    kinds: dict[int, set[str]] = {}
    for chat_id in rules.group_invite_rules:
        if rules.invite_rule_for(int(chat_id)) is not None:
            kinds.setdefault(int(chat_id), set()).add("invite")
    for chat_id in rules.group_activity_rules:
        if rules.activity_rule_for(int(chat_id)) is not None:
            kinds.setdefault(int(chat_id), set()).add("activity")
    for chat_id in rules.base_activity_rules:
        kinds.setdefault(int(chat_id), set()).add("activity")
    if rules.admission:
        kind = "invite" if rules.admission.type == "invite" else "activity"
        for chat_id in rules.admission.groups:
            kinds.setdefault(int(chat_id), set()).add(kind)
    return kinds


async def _permission_error(bot: Bot, chat_id: int, kinds: set[str]) -> str | None:
    try:
        chat = await bot.get_chat(chat_id)
        member = await bot.get_chat_member(chat_id, bot.id)
    except TelegramAPIError:
        return "Telegram 暂时无法核验机器人权限，请联系活动管理员"
    if str(member.status) not in {"administrator", "creator"}:
        return "机器人不在群内或已不是管理员，请联系活动管理员"
    if "invite" in kinds and getattr(member, "can_invite_users", True) is False:
        return "机器人缺少邀请用户权限，请联系活动管理员"
    if "activity" not in kinds:
        return None
    permissions = getattr(chat, "permissions", None)
    can_send = getattr(permissions, "can_send_messages", None)
    if can_send is False:
        return "群已禁止普通成员发言，当前无法累计发言加成"
    if can_send is not True:
        return "暂时无法确认普通成员发言权限，请联系活动管理员"
    return None
