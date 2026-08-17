from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.campaign_targets import require_campaign_admin
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.domain.enums import PrizeClaimState
from tg_lottery.infrastructure.db import Campaign, DrawRun, DrawSnapshot, Winner


class WinnerService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def mark_claimed(self, winner_id: UUID, actor_user_id: int) -> None:
        async with self._uow_factory() as uow:
            winner = await uow.session.scalar(
                select(Winner).where(Winner.id == winner_id).with_for_update()
            )
            if winner is None:
                raise ValueError("winner not found")
            if winner.claim_state is PrizeClaimState.CLAIMED:
                return
            campaign = await uow.session.scalar(
                select(Campaign)
                .join(DrawSnapshot, DrawSnapshot.campaign_id == Campaign.id)
                .join(DrawRun, DrawRun.snapshot_id == DrawSnapshot.id)
                .where(DrawRun.id == winner.draw_run_id)
            )
            if campaign is None:
                raise ValueError("winner campaign not found")
            await require_campaign_admin(
                uow.session, campaign_id=campaign.id, user_id=actor_user_id
            )
            winner.claim_state = PrizeClaimState.CLAIMED
            winner.claimed_at = utcnow()
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action="winner.claimed",
                aggregate_type="winner",
                aggregate_id=str(winner.id),
            )
