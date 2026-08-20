from datetime import timedelta
from uuid import UUID, uuid4

from tg_lottery.application.campaign_targets import require_campaign_admin
from tg_lottery.application.operational_blocks import reset_qualification_blocks_for_retry
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_audit, add_job, utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import Campaign

_SETTLE_DELAY_SECONDS = 1
_DRAW_DELAY_SECONDS = 2


async def retry_qualification_review(
    uow_factory: UnitOfWorkFactory, campaign_id: UUID, actor_user_id: int
) -> None:
    async with uow_factory() as uow:
        campaign = await uow.session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None:
            raise ValueError("campaign not found")
        await require_campaign_admin(uow.session, campaign_id=campaign.id, user_id=actor_user_id)
        if campaign.state is not CampaignState.SETTLING:
            raise ValueError("only settling campaigns can retry qualification review")
        count = await reset_qualification_blocks_for_retry(uow.session, campaign.id)
        if count == 0:
            raise ValueError("campaign has no qualification review block")
        _schedule_review_jobs(uow.session, campaign)
        add_audit(
            uow.session,
            actor_user_id=actor_user_id,
            action="qualification.manual_retry_requested",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            details={"blocked_targets": count},
        )


def _schedule_review_jobs(session: object, campaign: Campaign) -> None:
    now = utcnow()
    suffix = f"{campaign.id}:{uuid4().hex}"
    jobs = (
        ("qualification_review", now),
        ("campaign_settle", now + timedelta(seconds=_SETTLE_DELAY_SECONDS)),
        ("campaign_draw", now + timedelta(seconds=_DRAW_DELAY_SECONDS)),
    )
    for job_type, run_at in jobs:
        add_job(
            session,
            job_type=job_type,
            aggregate_id=str(campaign.id),
            business_key=f"{job_type}_manual_retry:{suffix}",
            run_at=run_at,
        )
