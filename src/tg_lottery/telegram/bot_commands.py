import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats

_LOGGER = logging.getLogger(__name__)

_PRIVATE_COMMANDS = (
    BotCommand(command="start", description="抽奖助手首页"),
    BotCommand(command="new", description="创建或继续原草稿"),
    BotCommand(command="index", description="我发起的抽奖"),
    BotCommand(command="join", description="我参加的抽奖"),
    BotCommand(command="help", description="使用指南与频道入口"),
    BotCommand(command="region", description="设置归属地区"),
    BotCommand(command="myid", description="查看我的用户ID"),
)
_GROUP_COMMANDS = (BotCommand(command="getid", description="查看本群ID"),)


async def register_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(list(_PRIVATE_COMMANDS))
        await bot.set_my_commands(list(_GROUP_COMMANDS), scope=BotCommandScopeAllGroupChats())
    except TelegramAPIError:
        _LOGGER.warning("bot command registration rejected", exc_info=True)
