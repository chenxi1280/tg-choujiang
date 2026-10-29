from tg_lottery.application.notice_recovery import cancel_unauthorized_shortcut
from tg_lottery.application.outbox_claims import OutboxClaimLost
from tg_lottery.application.retention_policy import cancel_retired_deletion
from tg_lottery.application.start_notices import cancel_closed_start
from tg_lottery.telegram.gateway_requests import build_gateway_call


async def prepare_owned_delivery(worker, outbox):
    try:
        call = await worker._prepare_gateway_call(outbox)
        if call is None:
            return None
        attempt = await worker._mark_gateway_started(outbox.id, claim=outbox)
        return call, attempt
    except OutboxClaimLost:
        worker._logger.warning(
            "Outbox pre-send claim lost; skipping", extra={"outbox_id": str(outbox.id)}
        )
        return None


async def prepare_call(session_factory, outbox):
    if await cancel_unauthorized_shortcut(session_factory, outbox):
        return None
    if await cancel_closed_start(session_factory, outbox):
        return None
    if await cancel_retired_deletion(session_factory, outbox):
        return None
    return await build_gateway_call(session_factory, outbox)


PROJECTED_OPERATIONS = frozenset(
    {
        "send_start_rules",
        "send_featured_campaign",
        "send_progress_notice",
        "send_campaign_card",
        "create_invite_link",
        "revoke_invite_link",
        "create_required_chat_invite",
        "revoke_required_chat_invite",
    }
)
