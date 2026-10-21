from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.campaign_references import resolve_campaign_reference
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.publication import campaign_card_payload
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.application.request_context import current_update
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, Prize, TelegramOutbox
from tg_lottery.presentation.participation_rules import rule_manifest


@dataclass(frozen=True)
class CampaignShareRequest:
    campaign_id: UUID
    user_id: int
    bot_username: str


async def request_campaign_share(factory: UnitOfWorkFactory, request: CampaignShareRequest) -> None:
    context = current_update()
    if context is None or request.user_id <= 0:
        raise ValueError("campaign sharing requires an authenticated private update")
    key = f"campaign_share:{context.bot_id}:{context.update_id}:{request.user_id}"
    async with factory() as uow:
        campaign = await uow.session.get(Campaign, request.campaign_id, with_for_update=True)
        if await uow.session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key == key)
        ):
            return
        _require_shareable(campaign)
        prizes = tuple(
            await uow.session.scalars(
                select(Prize).where(Prize.campaign_id == campaign.id).order_by(Prize.position)
            )
        )
        payload = campaign_card_payload(campaign, request.bot_username, prizes)
        text, _pages = rule_manifest(payload)
        add_outbox(
            uow.session,
            operation="send_campaign_share_card",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=request.user_id,
            business_key=key,
            payload=payload | {"rendered_card_text": text, "share_card": True},
        )


async def load_inline_share_payload(session_factory, reference, *, bot_username):
    async with session_factory() as session:
        campaign = await resolve_campaign_reference(session, reference)
        try:
            _require_shareable(campaign)
        except UserFacingError:
            return None
        prizes = tuple(
            await session.scalars(
                select(Prize).where(Prize.campaign_id == campaign.id).order_by(Prize.position)
            )
        )
        payload = campaign_card_payload(campaign, bot_username, prizes)
        text, _pages = rule_manifest(payload)
        return payload | {"rendered_card_text": text, "share_card": True}


def _require_shareable(campaign: Campaign | None) -> None:
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    if campaign.state not in {CampaignState.RUNNING, CampaignState.SCHEDULED}:
        raise UserFacingError("活动当前未开放参与, 请在活动规则中查看实际状态")
    if utcnow() > campaign.cutoff_at:
        raise UserFacingError("本期报名已截止, 请查看活动规则和开奖结果")
