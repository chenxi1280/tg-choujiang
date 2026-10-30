from dataclasses import dataclass

CHINESE_COMMANDS = {
    "/首页": "/menu",
    "/创建抽奖": "/new",
    "/我发起的抽奖": "/index",
    "/我参加的抽奖": "/join",
    "/使用指南": "/help",
    "/设置地区": "/region",
    "/我的编号": "/myid",
    "/本群编号": "/getid",
}

PRIVATE_BOT_COMMANDS = frozenset(
    {"/start", "/menu", "/new", "/index", "/join", "/help", "/region", "/myid"}
)
DEFERRED_CALLBACK_PREFIXES = (
    "preview:unpublished",
    "member:join:",
    "member:bonus:",
    "member:rules:",
    "member:tickets",
    "member:invites",
    "m:",
    "nav:joined:",
    "member:bonus_invites:",
    "member:bonus_activity:",
)


@dataclass(frozen=True)
class BotCommandInput:
    name: str
    argument: str
    recipient: str

    def addressed_to(self, bot_username: str) -> bool:
        return not self.recipient or self.recipient.casefold() == bot_username.casefold()


def parse_bot_command(text: str) -> BotCommandInput | None:
    parts = text.split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return None
    name, _, recipient = parts[0].partition("@")
    argument = parts[1] if len(parts) > 1 else ""
    return BotCommandInput(CHINESE_COMMANDS.get(name, name), argument, recipient)
