from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import (
    CampaignParticipationGroup,
    CampaignRequirement,
    Group,
    TelegramOutbox,
)


@dataclass(frozen=True)
class PublicationIssue:
    chat_id: int
    title: str
    state: OutboxState
    error: str
    operation: str = "send_campaign_card"


async def load_publication_issues(
    session_factory: async_sessionmaker[AsyncSession], campaign_id: UUID
) -> tuple[PublicationIssue, ...]:
    async with session_factory() as session:
        rows = await session.execute(_issue_statement(campaign_id))
        return tuple(
            PublicationIssue(
                int(chat_id),
                str(title or "群或频道名称待更新"),
                state,
                str(error or "未记录具体错误"),
                operation,
            )
            for chat_id, title, state, error, operation in rows
        )


def _issue_statement(campaign_id: UUID):
    return (
        select(
            TelegramOutbox.recipient_chat_id,
            func.coalesce(
                CampaignParticipationGroup.title_snapshot, CampaignRequirement.title, Group.title
            ),
            TelegramOutbox.state,
            TelegramOutbox.last_error,
            TelegramOutbox.operation,
        )
        .select_from(TelegramOutbox)
        .outerjoin(
            CampaignRequirement,
            (CampaignRequirement.campaign_id == campaign_id)
            & (CampaignRequirement.chat_id_snapshot == TelegramOutbox.recipient_chat_id),
        )
        .outerjoin(Group, Group.chat_id == TelegramOutbox.recipient_chat_id)
        .outerjoin(
            CampaignParticipationGroup,
            (CampaignParticipationGroup.campaign_id == campaign_id)
            & or_(
                CampaignParticipationGroup.chat_id_snapshot == TelegramOutbox.recipient_chat_id,
                CampaignParticipationGroup.group_id == Group.id,
            ),
        )
        .where(
            TelegramOutbox.aggregate_type == "campaign",
            TelegramOutbox.aggregate_id == str(campaign_id),
            TelegramOutbox.operation.in_(
                ("send_campaign_card", "send_campaign_rules", "create_required_chat_invite")
            ),
            TelegramOutbox.state.in_((OutboxState.PRE_ACCEPT_FAILED, OutboxState.UNKNOWN_HOLD)),
        )
        .order_by(TelegramOutbox.recipient_chat_id)
    )
