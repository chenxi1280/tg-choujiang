from sqlalchemy import select

from tg_lottery.infrastructure.db import Campaign, CampaignParticipationGroup, GroupAdmin


def cached_management_scope(user_id):
    has_targets = (
        select(CampaignParticipationGroup.id)
        .where(CampaignParticipationGroup.campaign_id == Campaign.id)
        .correlate(Campaign)
        .exists()
    )
    target_admin = (
        select(GroupAdmin.id)
        .where(
            GroupAdmin.group_id == CampaignParticipationGroup.group_id,
            GroupAdmin.user_id == user_id,
            GroupAdmin.active.is_(True),
        )
        .correlate(CampaignParticipationGroup)
        .exists()
    )
    missing = (
        select(CampaignParticipationGroup.id)
        .where(CampaignParticipationGroup.campaign_id == Campaign.id, ~target_admin)
        .correlate(Campaign)
        .exists()
    )
    legacy_admin = (
        select(GroupAdmin.id)
        .where(
            GroupAdmin.group_id == Campaign.group_id,
            GroupAdmin.user_id == user_id,
            GroupAdmin.active.is_(True),
        )
        .correlate(Campaign)
        .exists()
    )
    return (has_targets & ~missing) | (~has_targets & legacy_admin)
