from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import CampaignParticipationGroup, Group, TelegramOutbox


@dataclass(frozen=True)
class PublicationIssue:
    chat_id: int
    title: str
    state: OutboxState
    error: str


async def load_publication_issues(
    session_factory: async_sessionmaker[AsyncSession], campaign_id: UUID
) -> tuple[PublicationIssue, ...]:
    return await _load_target_issues(session_factory, campaign_id, "send_campaign_card")


async def _load_target_issues(
    session_factory: async_sessionmaker[AsyncSession],
    campaign_id: UUID,
    operation: str,
) -> tuple[PublicationIssue, ...]:
    async with session_factory() as session:
        rows = await session.execute(
            select(
                TelegramOutbox.recipient_chat_id,
                CampaignParticipationGroup.title_snapshot,
                TelegramOutbox.state,
                TelegramOutbox.last_error,
            )
            .outerjoin(Group, Group.chat_id == TelegramOutbox.recipient_chat_id)
            .outerjoin(
                CampaignParticipationGroup,
                (CampaignParticipationGroup.campaign_id == campaign_id)
                & (
                    or_(
                        CampaignParticipationGroup.chat_id_snapshot
                        == TelegramOutbox.recipient_chat_id,
                        CampaignParticipationGroup.group_id == Group.id,
                    )
                ),
            )
            .where(
                TelegramOutbox.aggregate_type == "campaign",
                TelegramOutbox.aggregate_id == str(campaign_id),
                TelegramOutbox.operation == operation,
                TelegramOutbox.state.in_((OutboxState.PRE_ACCEPT_FAILED, OutboxState.UNKNOWN_HOLD)),
            )
            .order_by(TelegramOutbox.recipient_chat_id)
        )
        return tuple(
            PublicationIssue(
                int(chat_id),
                str(title or chat_id),
                state,
                str(error or "未记录具体错误"),
            )
            for chat_id, title, state, error in rows
        )
