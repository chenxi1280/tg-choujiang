from dataclasses import dataclass

from sqlalchemy import select

from tg_lottery.application.campaign_query_targets import load_campaign_targets
from tg_lottery.infrastructure.db import CampaignOperationalBlock


@dataclass(frozen=True)
class OperationalIssue:
    chat_id: int
    title: str
    error_code: str
    details: str | None
    source: str


async def load_operational_issues(session, campaign_id) -> tuple[OperationalIssue, ...]:
    blocks = tuple(
        await session.scalars(
            select(CampaignOperationalBlock)
            .where(
                CampaignOperationalBlock.campaign_id == campaign_id,
                CampaignOperationalBlock.resolved_at.is_(None),
            )
            .order_by(CampaignOperationalBlock.first_detected_at, CampaignOperationalBlock.id)
        )
    )
    if not blocks:
        return ()
    targets = await load_campaign_targets(session, campaign_id)
    names = {
        (target.kind, chat_id): target.title
        for target in targets
        for chat_id in (target.chat_id, target.rule_chat_id)
    }
    return tuple(
        OperationalIssue(
            block.chat_id,
            names.get((block.target_kind, block.chat_id), f"目标群/频道 {block.chat_id}"),
            block.error_code,
            block.details,
            block.source,
        )
        for block in blocks
    )
