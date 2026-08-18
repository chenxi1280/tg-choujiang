from aiogram import Bot

from tg_lottery.domain.errors import AuthorizationDenied


async def verify_chat_admin(
    bot: Bot, chat_id: int, user_id: int, *, require_invites: bool = False
) -> None:
    user = await bot.get_chat_member(chat_id, user_id)
    bot_member = await bot.get_chat_member(chat_id, bot.id)
    if not _is_admin(user):
        raise AuthorizationDenied("当前 Telegram 管理员身份无效：你不是该群管理员")
    if not _is_admin(bot_member):
        raise AuthorizationDenied(
            "当前 Telegram 管理员身份无效：请先把机器人加入群并设为管理员，然后返回刷新"
        )
    if require_invites and not bool(getattr(bot_member, "can_invite_users", False)):
        raise AuthorizationDenied(
            "机器人缺少管理邀请链接权限。请在群管理员设置中开启邀请权限后重试"
        )


def _is_admin(member: object) -> bool:
    return str(getattr(member, "status", "")) in {"administrator", "creator"}
