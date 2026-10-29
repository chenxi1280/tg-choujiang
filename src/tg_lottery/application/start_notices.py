"""One durable first-start notification chain per originally published group."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update

from tg_lottery.application.outbox_claims import lock_presend_claim
from tg_lottery.application.public_participation import public_participation
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox
from tg_lottery.presentation.participation_rules import rule_manifest

START_NOTICE_OPERATIONS = ("send_start_rules", "send_start_card")
PAUSED_NOTICE_REASON = "start_notice: paused before sending"


async def resume_start_notices(session, campaign):
    if campaign.state != CampaignState.RUNNING or not campaign.presentation.get(
        "start_notice_intent"
    ):
        return
    await queue_start_notices(session, campaign)
    await session.execute(
        update(TelegramOutbox)
        .where(
            TelegramOutbox.aggregate_type == "campaign",
            TelegramOutbox.aggregate_id == str(campaign.id),
            TelegramOutbox.operation.in_(START_NOTICE_OPERATIONS),
            TelegramOutbox.state == OutboxState.RETRY_SCHEDULED,
            TelegramOutbox.gateway_started_at.is_(None),
            TelegramOutbox.lease_owner.is_(None),
            TelegramOutbox.lease_until.is_(None),
            TelegramOutbox.last_error == PAUSED_NOTICE_REASON,
        )
        .values(run_at=utcnow())
    )


async def queue_start_notices(session, campaign):
    if utcnow() >= campaign.cutoff_at:
        return
    if not campaign.presentation.get("start_notice_intent"):
        campaign.presentation = campaign.presentation | {
            "start_notice_intent": utcnow().isoformat()
        }
    sources = list(
        await session.scalars(
            select(TelegramOutbox).where(
                TelegramOutbox.aggregate_id == str(campaign.id),
                TelegramOutbox.operation == "send_campaign_card",
                TelegramOutbox.state == OutboxState.CONFIRMED,
                TelegramOutbox.remote_message_id.is_not(None),
            )
        )
    )
    for source in sources:
        await queue_start_target(session, campaign, source)


async def queue_start_target(session, campaign, source):
    key = f"start_card:{campaign.id}:{source.recipient_chat_id}"
    if await session.scalar(
        select(TelegramOutbox.id).where(TelegramOutbox.business_key.startswith(key + ":"))
    ):
        return
    payload = dict(source.payload) | {
        "presentation": campaign.presentation | {"start_at": campaign.preheat_at.isoformat()},
        "name": campaign.name,
        "rules": campaign.rules,
        "timezone": campaign.timezone,
        "cutoff_at": campaign.cutoff_at.isoformat(),
        "draw_at": campaign.draw_at.isoformat(),
        "campaign_id": str(campaign.id),
        "source_outbox_id": str(source.id),
        "public_participation": await public_participation(session, campaign.id),
        "started_at": campaign.presentation["start_notice_intent"],
    }
    text, pages = rule_manifest(payload)
    payload = payload | {
        "rendered_card_text": "抽奖活动已经开始\n@全体成员\n\n" + text,
        "start_pages": list(pages),
        "page_index": 0,
        "start_key": key,
    }
    _queue_part(session, campaign, chat_id=source.recipient_chat_id, payload=payload)


def _queue_part(session, campaign, *, chat_id, payload):
    index = payload["page_index"]
    pages = payload["start_pages"]
    operation = "send_start_rules" if index < len(pages) else "send_start_card"
    if operation == "send_start_rules":
        payload = payload | {"text": f"完整抽奖信息 {index + 1}/{len(pages)}\n" + pages[index]}
    add_outbox(
        session,
        operation=operation,
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=chat_id,
        business_key=f"{payload['start_key']}:{index}",
        payload=payload,
    )


async def project_start_notice(session, row):
    if row.operation not in {"send_start_rules", "send_start_card"}:
        return False
    if row.operation == "send_start_card":
        return True
    campaign = await session.get(Campaign, UUID(row.aggregate_id), with_for_update=True)
    if campaign is None:
        raise ValueError("start notice campaign missing")
    if (
        campaign.state not in {CampaignState.RUNNING, CampaignState.PAUSED}
        or utcnow() >= campaign.cutoff_at
    ):
        return True
    payload = dict(row.payload) | {"page_index": row.payload["page_index"] + 1}
    next_key = f"{payload['start_key']}:{payload['page_index']}"
    if not await session.scalar(
        select(TelegramOutbox.id).where(TelegramOutbox.business_key == next_key)
    ):
        _queue_part(session, campaign, chat_id=row.recipient_chat_id, payload=payload)
    return True


async def cancel_closed_start(session_factory, outbox):
    """Skip the gateway for closed notices or durably defer a paused chain."""
    if outbox.operation not in START_NOTICE_OPERATIONS:
        return False
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, UUID(outbox.aggregate_id), with_for_update=True)
        source_id = outbox.payload.get("source_outbox_id")
        source = await session.get(TelegramOutbox, UUID(source_id)) if source_id else None
        if _start_authorized(campaign, source, outbox):
            if campaign.state != CampaignState.PAUSED:
                return False
            row = await lock_presend_claim(session, outbox)
            row.state = OutboxState.RETRY_SCHEDULED
            row.run_at = min(utcnow() + timedelta(seconds=30), campaign.cutoff_at)
            row.last_error = PAUSED_NOTICE_REASON
            row.lease_owner = row.lease_until = None
            return True
        row = await lock_presend_claim(session, outbox)
        row.state = OutboxState.CANCELLED
        row.last_error = (
            "start_notice: activity closed or original publication no longer authorized"
        )
        row.lease_owner = None
        row.lease_until = None
        return True


def _start_authorized(campaign, source, outbox):
    return (
        campaign is not None
        and campaign.state in {CampaignState.RUNNING, CampaignState.PAUSED}
        and utcnow() < campaign.cutoff_at
        and source is not None
        and source.operation == "send_campaign_card"
        and source.state == OutboxState.CONFIRMED
        and source.aggregate_id == str(campaign.id)
        and source.recipient_chat_id == outbox.recipient_chat_id
        and source.remote_message_id is not None
    )
