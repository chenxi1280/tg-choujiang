from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.application.campaign_targets import require_campaign_admin
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.domain.action_confirmation import ExpectedCampaign, validate_expected
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Group,
    TelegramOutbox,
)


async def retry_failed_publication(
    uow_factory: UnitOfWorkFactory,
    campaign_id: UUID,
    actor_user_id: int,
    *,
    expected: ExpectedCampaign | None = None,
) -> int:
    async with uow_factory() as uow:
        campaign = await uow.session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None:
            raise UserFacingError("活动不存在或已经失效")
        validate_expected(campaign, expected)
        await require_campaign_admin(uow.session, campaign_id=campaign_id, user_id=actor_user_id)
        if campaign.state is not CampaignState.PUBLISHING:
            raise UserFacingError("活动当前不在投放中, 不能重试发布")
        if campaign.cutoff_at <= utcnow():
            raise UserFacingError("活动已过截止时间, 无法重新发布; 请取消此活动后创建新的抽奖")
        rows = (
            await uow.session.scalars(
                select(TelegramOutbox)
                .where(
                    TelegramOutbox.aggregate_type == "campaign",
                    TelegramOutbox.aggregate_id == str(campaign_id),
                    TelegramOutbox.operation.in_(
                        ("send_campaign_card", "send_campaign_rules", "create_required_chat_invite")
                    ),
                    TelegramOutbox.state == OutboxState.PRE_ACCEPT_FAILED,
                )
                .with_for_update()
            )
        ).all()
        if not rows:
            raise UserFacingError("当前没有可安全重试的明确失败群")
        for row in rows:
            _reset_failed_outbox(row)
            await _mark_target_pending(uow.session, campaign_id, row.recipient_chat_id)
        add_audit(
            uow.session,
            actor_user_id=actor_user_id,
            action="campaign.publication_retry_failed",
            aggregate_type="campaign",
            aggregate_id=str(campaign_id),
            reason=f"retry_count={len(rows)}",
        )
        return len(rows)


def _reset_failed_outbox(row: TelegramOutbox) -> None:
    row.state = OutboxState.PENDING
    row.run_at = utcnow()
    row.lease_owner = None
    row.lease_until = None
    row.gateway_started_at = None


async def _mark_target_pending(session: AsyncSession, campaign_id: UUID, chat_id: int) -> None:
    target = await session.scalar(
        select(CampaignParticipationGroup)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            or_(
                CampaignParticipationGroup.chat_id_snapshot == chat_id,
                Group.chat_id == chat_id,
            ),
        )
    )
    if target is not None and target.publication_state == "failed":
        target.publication_state = "pending"
