from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    GroupAdmin,
    Participant,
    Prize,
    RuleRevision,
)


async def seed_campaign(
    session: AsyncSession,
    *,
    state: CampaignState = CampaignState.RUNNING,
    rules: CampaignRules | None = None,
    cutoff_at: datetime | None = None,
) -> tuple[Group, Campaign, Participant]:
    now = datetime.now(UTC)
    group = Group(chat_id=-100123, title="测试群", timezone="Asia/Shanghai")
    session.add(group)
    await session.flush()
    session.add(GroupAdmin(group_id=group.id, user_id=101, source="owner", active=True))
    campaign_rules = rules or CampaignRules()
    campaign = _campaign(
        group,
        rules=campaign_rules,
        state=state,
        cutoff_at=cutoff_at,
        now=now,
    )
    session.add(campaign)
    await session.flush()
    _add_campaign_config(session, campaign)
    participant = _participant(campaign)
    session.add(participant)
    await session.flush()
    return group, campaign, participant


def _campaign(
    group: Group,
    *,
    rules: CampaignRules,
    state: CampaignState,
    cutoff_at: datetime | None,
    now: datetime,
) -> Campaign:
    cutoff = cutoff_at or now + timedelta(days=1)
    return Campaign(
        group_id=group.id,
        name="测试活动",
        state=state,
        timezone=rules.timezone,
        preheat_at=now - timedelta(days=1),
        cutoff_at=cutoff,
        draw_at=cutoff + timedelta(minutes=10),
        rules=rules.model_dump(mode="json"),
        config_revision=1,
        created_by=101,
    )


def _add_campaign_config(session: AsyncSession, campaign: Campaign) -> None:
    session.add(
        RuleRevision(
            campaign_id=campaign.id,
            revision=1,
            rules=campaign.rules,
            rules_hash="a" * 64,
            frozen=True,
            actor_user_id=101,
        )
    )
    session.add(
        Prize(
            campaign_id=campaign.id,
            position=1,
            name="一等奖",
            quantity=1,
            claim_instructions="联系管理员",
        )
    )


def _participant(campaign: Campaign) -> Participant:
    return Participant(
        campaign_id=campaign.id,
        user_id=201,
        display_name="成员一",
        private_reachable=True,
        qualification_state=QualificationState.ELIGIBLE,
        registered_revision=1,
    )
