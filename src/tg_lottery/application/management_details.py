from dataclasses import dataclass

from sqlalchemy import func, select

from tg_lottery.application.admission_qualification import behavior_clause
from tg_lottery.domain.enums import OutboxState, QualificationState
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    DrawEntry,
    DrawSnapshot,
    Group,
    Participant,
    TelegramOutbox,
)


@dataclass(frozen=True)
class TargetPublication:
    title: str
    card_state: str
    required_pages: int | None
    confirmed_pages: int
    failed_pages: int
    unknown_pages: int


@dataclass(frozen=True)
class ManagementDetails:
    eligible: int
    frozen: int | None
    targets: tuple[TargetPublication, ...]


async def load_management_details(factory, campaign_id):
    async with factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        eligible = await session.scalar(
            select(func.count(Participant.id)).where(
                Participant.campaign_id == campaign_id,
                behavior_clause(),
                Participant.qualification_state == QualificationState.ELIGIBLE,
            )
        )
        snapshot_id = await session.scalar(
            select(DrawSnapshot.id).where(DrawSnapshot.campaign_id == campaign_id)
        )
        frozen = (
            await session.scalar(
                select(func.count(DrawEntry.id)).where(DrawEntry.snapshot_id == snapshot_id)
            )
            if snapshot_id is not None
            else None
        )
        targets = await _target_publications(session, campaign)
        return ManagementDetails(int(eligible or 0), frozen, targets)


async def _target_publications(session, campaign):
    targets = await session.execute(
        select(CampaignParticipationGroup, Group.chat_id)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(CampaignParticipationGroup.campaign_id == campaign.id)
        .order_by(CampaignParticipationGroup.position)
    )
    pages = await session.execute(
        select(
            TelegramOutbox.recipient_chat_id, TelegramOutbox.state, func.count(TelegramOutbox.id)
        )
        .where(
            TelegramOutbox.aggregate_id == str(campaign.id),
            TelegramOutbox.operation == "send_campaign_rules",
        )
        .group_by(TelegramOutbox.recipient_chat_id, TelegramOutbox.state)
    )
    counts = {}
    for chat, state, count in pages:
        counts.setdefault(chat, {})[state] = int(count)
    manifests = campaign.presentation.get("publication_rule_manifest") or {}
    return tuple(
        _target_status(row, counts.get(row.chat_id_snapshot, {}), manifests) for row, _ in targets
    )


def _target_status(row, counts, manifests):
    key = str(row.chat_id_snapshot)
    expected = len(manifests[key]["pages"]) if key in manifests else None
    return TargetPublication(
        row.title_snapshot,
        row.publication_state,
        expected,
        counts.get(OutboxState.CONFIRMED, 0),
        counts.get(OutboxState.PRE_ACCEPT_FAILED, 0),
        counts.get(OutboxState.UNKNOWN_HOLD, 0),
    )
