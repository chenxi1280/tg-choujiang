from sqlalchemy import select

from tg_lottery.application.progress_notifications import queue_progress_notice
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import Campaign, Participant


async def notify_threshold_crossing(session, observation, *, count):
    rows = await session.execute(
        select(Campaign, Participant)
        .join(Participant, Participant.campaign_id == Campaign.id)
        .where(
            Participant.user_id == observation.user_id,
            Campaign.state.in_([CampaignState.RUNNING, CampaignState.SETTLING]),
            Campaign.preheat_at <= observation.event_at,
            Campaign.cutoff_at >= observation.event_at,
        )
    )
    for campaign, participant in rows:
        if campaign.rules.get("participation_rules_version") == "v3.0":
            continue
        threshold = (campaign.rules.get("message_thresholds") or {}).get(str(observation.chat_id))
        if threshold is None or count != int(threshold):
            continue
        await queue_progress_notice(
            session,
            campaign=campaign,
            participant=participant,
            chat_id=observation.chat_id,
            key=f"message_threshold:{participant.id}:{observation.chat_id}:{threshold}",
            text=f"本群累计发言条件已达标: {count}/{threshold} 条, 请在活动入口验证全部参与条件。",
        )
