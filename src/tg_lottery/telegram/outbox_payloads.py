from collections.abc import Callable

from tg_lottery.domain.public_refs import (
    is_neutral_invite_link_name,
    new_invite_link_name,
)
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import TelegramOutbox

InviteNameFactory = Callable[[], str]


def normalize_pending_invite_name(
    row: TelegramOutbox,
    name_factory: InviteNameFactory | None = None,
) -> None:
    if row.operation != "create_invite_link":
        return
    current_name = str(row.payload.get("name") or "")
    if is_neutral_invite_link_name(current_name):
        return
    name = (name_factory or new_invite_link_name)()
    if not is_neutral_invite_link_name(name):
        raise ValueError("invite link name is invalid")
    payload = dict(row.payload)
    payload["name"] = name
    row.payload = payload
    row.request_hash = canonical_hash(payload)
