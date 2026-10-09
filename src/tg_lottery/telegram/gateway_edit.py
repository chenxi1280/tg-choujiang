"""Confirm an edit only from a remote result or an explicit identical-content reply."""

from tg_lottery.telegram.panel_edit import idempotent_edit
from tg_lottery.telegram.remote_serialization import remote_object


async def confirmed_edit(operation, *, message_id: int):
    from tg_lottery.telegram.gateway import GatewayResult

    result = await idempotent_edit(operation)
    remote = (
        {"message_id": message_id, "unchanged": True} if result is None else remote_object(result)
    )
    return GatewayResult(remote_result=remote, remote_message_id=message_id)
