"""Read-only activity discovery and the three-part personal progress projection."""

from sqlalchemy import or_, select

from tg_lottery.application.campaign_query_targets import active_campaigns_by_chat
from tg_lottery.application.public_participation import successful_registration
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import Campaign, Participant

CURRENT_STATES = (
    CampaignState.SCHEDULED,
    CampaignState.RUNNING,
    CampaignState.PAUSED,
    CampaignState.SETTLING,
    CampaignState.READY_TO_DRAW,
    CampaignState.DRAWING,
)


async def query_campaigns(session_factory, *, chat_id, user_id=None):
    async with session_factory() as session:
        if chat_id is not None:
            rows = await active_campaigns_by_chat(session, chat_id)
            rows = [row for row in rows if row.state in CURRENT_STATES]
        else:
            rows = list(
                await session.scalars(
                    select(Campaign)
                    .where(Campaign.state.in_(CURRENT_STATES))
                    .join(Participant, Participant.campaign_id == Campaign.id)
                    .where(
                        Participant.user_id == user_id,
                        or_(
                            Participant.registration_intent_at.is_not(None),
                            successful_registration(),
                        ),
                    )
                )
            )
        if user_id is not None and chat_id is not None:
            ids = set(
                await session.scalars(
                    select(Participant.campaign_id).where(
                        Participant.user_id == user_id,
                        Participant.campaign_id.in_([row.id for row in rows]),
                        or_(
                            Participant.registration_intent_at.is_not(None),
                            successful_registration(),
                        ),
                    )
                )
            )
            rows = [row for row in rows if row.id in ids]
        return tuple(sorted(rows, key=lambda row: (row.created_at, str(row.id)), reverse=True))
