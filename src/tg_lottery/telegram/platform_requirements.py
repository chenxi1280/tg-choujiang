from aiogram.exceptions import TelegramAPIError

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.platform_policy import merge_official_targets


async def inspect_official_channels(bot, policy):
    if policy is None:
        return
    if int(bot.id) != policy.bot_id:
        raise UserFacingError("平台策略与当前机器人身份不一致，暂不能发布")
    errors = []
    for channel in policy.channels:
        try:
            await _inspect_channel(bot, channel, bot_id=policy.bot_id)
        except UserFacingError as exc:
            errors.append(f"· {channel.title}：{exc.public_message}")
        except TelegramAPIError as exc:
            errors.append(
                f"· {channel.title}：Telegram核验失败（{type(exc).__name__}），请修复后重试"
            )
    if errors:
        raise UserFacingError("平台频道配置待修复，暂不能发布：\n" + "\n".join(errors))


async def _inspect_channel(bot, channel, *, bot_id):
    chat = await bot.get_chat(channel.chat_id)
    member = await bot.get_chat_member(channel.chat_id, bot_id)
    if int(chat.id) != channel.chat_id or str(chat.type) != "channel":
        raise UserFacingError("冻结身份不是对应频道")
    if str(member.status) not in {"administrator", "creator"}:
        raise UserFacingError("机器人需要频道管理员权限")
    if "/+" in channel.join_url:
        if (
            str(member.status) != "creator"
            and getattr(member, "can_invite_users", None) is not True
        ):
            raise UserFacingError("私有官方频道需要机器人创建加入入口的权限")
        return
    username = str(getattr(chat, "username", "") or "")
    if channel.join_url.rsplit("/", 1)[1].casefold() != username.casefold():
        raise UserFacingError("频道加入入口与当前身份不一致")


def attach_platform_policy(data, policy):
    if data.get("published_campaign_id") or data.get("publish_snapshot"):
        return data
    value = policy.model_dump(mode="json") if policy else None
    return data | {"platform_policy": value}


def editable_requirement_data(data, policy):
    if policy is None:
        return data
    identifiers = set(policy.chat_ids)
    aliases = {
        "@" + item.join_url.rsplit("/", 1)[1].casefold()
        for item in policy.channels
        if "/+" not in item.join_url
    }
    refs = data.get("required_chat_refs") or data.get("required_chat_ids") or []
    platform_only = {
        int(item["id"])
        for item in data.get("required_chats") or []
        if item.get("sources") == ["platform"]
    }
    overlap = _creator_overlap(refs, policy, platform_only=platform_only)
    refs = [ref for ref in refs if ref not in identifiers and str(ref).casefold() not in aliases]
    return data | {
        "required_chat_refs": refs,
        "required_chat_ids": refs,
        "platform_creator_overlap": overlap,
    }


def frozen_requirement_data(data, requirements, policy):
    combined = merge_official_targets(requirements, policy)
    for item in combined:
        if item["id"] in data.get("platform_creator_overlap", []):
            item["sources"] = ["platform", "creator"]
    return attach_platform_policy(data, policy) | {
        "required_chat_ids": [item["id"] for item in combined],
        "required_chats": combined,
    }


def platform_summary(data):
    policy = data.get("platform_policy")
    if not policy:
        return ""
    names = "、".join(item["title"] for item in policy["channels"])
    count = len(policy["channels"])
    return f"平台必备条件（只读）：启动本机器人 + 关注 {count} 个官方频道：{names}。\n"


def _creator_overlap(refs, policy, *, platform_only):
    aliases = {str(ref).casefold() for ref in refs}
    matching = [
        item.chat_id
        for item in policy.channels
        if item.chat_id in refs or "@" + item.join_url.rsplit("/", 1)[1].casefold() in aliases
    ]
    return [chat_id for chat_id in matching if chat_id not in platform_only]


def official_channel_for_ref(data, ref):
    for item in (data.get("platform_policy") or {}).get("channels", []):
        alias = "@" + item["join_url"].rsplit("/", 1)[1].casefold()
        if ref == item["chat_id"] or str(ref).casefold() == alias:
            return item
    return None
