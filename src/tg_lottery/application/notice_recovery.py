"""Retry only known unsent auxiliary notices, retaining the original business key."""

from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.campaign_targets import require_campaign_admin
from tg_lottery.application.outbox_claims import lock_presend_claim
from tg_lottery.application.publication_recovery import _reset_failed_outbox
from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.domain.action_confirmation import validate_expected
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox

NOTICE_OPERATIONS = ("send_channel_shortcut", "send_start_rules", "send_start_card")
NOTICE_STATES = {CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED}


async def retry_failed_notices(uow_factory, campaign_id, actor_user_id, *, expected=None):
    async with uow_factory() as uow:
        campaign = await uow.session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        validate_expected(campaign, expected)
        await require_campaign_admin(uow.session, campaign_id=campaign_id, user_id=actor_user_id)
        if campaign.state not in NOTICE_STATES or campaign.cutoff_at <= utcnow():
            raise UserFacingError("当前活动已停止接受通知重试")
        rows = await uow.session.scalars(
            select(TelegramOutbox)
            .where(
                TelegramOutbox.aggregate_type == "campaign",
                TelegramOutbox.aggregate_id == str(campaign_id),
                TelegramOutbox.operation.in_(NOTICE_OPERATIONS),
                TelegramOutbox.state == OutboxState.PRE_ACCEPT_FAILED,
            )
            .with_for_update()
        )
        retried = []
        for row in rows:
            if await notice_authorized(uow.session, campaign, row):
                _reset_failed_outbox(row)
                retried.append(str(row.id))
        if not retried:
            raise UserFacingError("没有可安全重试的明确失败通知; 暂停中的开始通知须恢复后重试")
        add_audit(
            uow.session,
            actor_user_id=actor_user_id,
            action="campaign.notice_retry_failed",
            aggregate_type="campaign",
            aggregate_id=str(campaign_id),
            reason="outbox_ids=" + ",".join(retried),
        )
        return len(retried)


async def notice_authorized(session, campaign, row):
    source_id = row.payload.get("source_outbox_id")
    source = await session.get(TelegramOutbox, UUID(source_id)) if source_id else None
    if not source or source.state != OutboxState.CONFIRMED or source.remote_message_id is None:
        return False
    if source.aggregate_id != str(campaign.id) or source.recipient_chat_id != row.recipient_chat_id:
        return False
    if row.operation != "send_channel_shortcut":
        return campaign.state == CampaignState.RUNNING and source.operation == "send_campaign_card"
    return campaign.presentation.get("ux_policy_version") == "r14" and source.operation in {
        "send_campaign_card",
        "send_featured_campaign",
    }


async def cancel_unauthorized_shortcut(session_factory, outbox):
    if outbox.operation != "send_channel_shortcut":
        return False
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, UUID(outbox.aggregate_id))
        allowed = (
            campaign is not None
            and campaign.state in NOTICE_STATES
            and campaign.cutoff_at > utcnow()
            and await notice_authorized(session, campaign, outbox)
        )
        if allowed:
            return False
        row = await lock_presend_claim(session, outbox)
        row.state = OutboxState.CANCELLED
        row.last_error = (
            "channel_shortcut: activity closed or original publication no longer authorized"
        )
        row.lease_owner = row.lease_until = None
        return True
