from dataclasses import dataclass
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from tg_lottery.application.activity_rules import activity_chat_ids
from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import Campaign, DailyProgress, Group, Participant


@dataclass(frozen=True)
class LegacyActivityProgress:
    local_date: date
    groups: tuple[tuple[str, int], ...]
    awarded_today: int
    group_counts: tuple[tuple[int, int], ...] = ()


async def load_legacy_activity_progress(factory, campaign_id: UUID, user_id: int):
    async with factory() as session:
        record = (
            await session.execute(
                select(Campaign, Participant, Group.chat_id)
                .join(Participant, Participant.campaign_id == Campaign.id)
                .join(Group, Group.id == Campaign.group_id)
                .where(Campaign.id == campaign_id, Participant.user_id == user_id)
            )
        ).one_or_none()
        if record is None:
            return None
        campaign, participant, main_chat = record
        local_date = utcnow().astimezone(ZoneInfo(campaign.timezone)).date()
        row = await session.scalar(
            select(DailyProgress).where(
                DailyProgress.campaign_id == campaign_id,
                DailyProgress.participant_id == participant.id,
                DailyProgress.participation_group_id.is_(None),
                DailyProgress.local_date == local_date,
            )
        )
        chat_ids = activity_chat_ids(campaign, main_chat)
        titles = dict(
            (
                await session.execute(
                    select(Group.chat_id, Group.title).where(Group.chat_id.in_(chat_ids))
                )
            ).all()
        )
        counts = row.valid_message_counts if row else {}
        groups = tuple(
            (titles.get(chat_id) or f"参与群{index}", int(counts.get(str(chat_id), 0)))
            for index, chat_id in enumerate(chat_ids, 1)
        )
        return LegacyActivityProgress(
            local_date,
            groups,
            row.tickets_awarded if row else 0,
            tuple((chat_id, int(counts.get(str(chat_id), 0))) for chat_id in chat_ids),
        )
