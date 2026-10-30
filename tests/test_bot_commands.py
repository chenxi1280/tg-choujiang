from aiogram.types import BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from tg_lottery.telegram.bot_commands import register_bot_commands


class FakeBot:
    def __init__(self):
        self.calls = []

    async def set_my_commands(self, commands, scope=None):
        self.calls.append((commands, scope))


async def test_register_commands_sets_private_and_group_scopes():
    bot = FakeBot()
    await register_bot_commands(bot)
    assert len(bot.calls) == 3
    assert bot.calls[0] == ([], None)
    private, scope = bot.calls[1]
    assert [c.command for c in private] == ["start"]
    assert isinstance(scope, BotCommandScopeAllPrivateChats)
    group, scope = bot.calls[2]
    assert group == []
    assert isinstance(scope, BotCommandScopeAllGroupChats)
