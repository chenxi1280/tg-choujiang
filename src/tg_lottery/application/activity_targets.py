from sqlalchemy import or_, select

from tg_lottery.infrastructure.db import Campaign, CampaignParticipationGroup, Group


async def locked_message_campaign(session: object, campaign_id: object) -> Campaign:
    return await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def message_rule_chat_id(session: object, campaign_id: object, chat_id: int) -> int:
    snapshot = await session.scalar(
        select(CampaignParticipationGroup.chat_id_snapshot)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            or_(Group.chat_id == chat_id, CampaignParticipationGroup.chat_id_snapshot == chat_id),
        )
    )
    return int(snapshot) if snapshot is not None else chat_id
