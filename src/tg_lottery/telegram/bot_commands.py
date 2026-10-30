import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

_LOGGER = logging.getLogger(__name__)

_PRIVATE_COMMANDS = (BotCommand(command="start", description="抽奖助手首页"),)


async def register_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands([])
        await bot.set_my_commands(list(_PRIVATE_COMMANDS), scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands([], scope=BotCommandScopeAllGroupChats())
    except TelegramAPIError:
        _LOGGER.warning("bot command registration rejected", exc_info=True)
