from tg_lottery.telegram.bot_commands import register_bot_commands


class FakeBot:
    def __init__(self):
        self.calls = []

    async def set_my_commands(self, commands, scope=None):
        self.calls.append((commands, scope))


async def test_register_commands_sets_private_and_group_scopes():
    bot = FakeBot()
    await register_bot_commands(bot)
    assert len(bot.calls) == 2
    private = bot.calls[0][0]
    assert [c.command for c in private] == [
        "progress",
        "start",
        "new",
        "index",
        "join",
        "help",
        "region",
        "myid",
    ]
    group = bot.calls[1][0]
    assert [c.command for c in group] == ["lottery", "progress", "getid"]
    assert bot.calls[1][1] is not None  # group scope
