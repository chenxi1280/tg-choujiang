from dataclasses import dataclass

PRIVATE_BOT_COMMANDS = frozenset(
    {"/start", "/menu", "/new", "/index", "/join", "/help", "/region", "/myid"}
)
DEFERRED_CALLBACK_PREFIXES = (
    "member:join:",
    "member:bonus:",
    "member:rules:",
    "member:tickets",
    "member:invites",
    "m:ir:",
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
    return BotCommandInput(name, argument, recipient)
