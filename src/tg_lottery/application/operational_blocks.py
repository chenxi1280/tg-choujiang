from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import (
    CampaignOperationalBlock,
    CampaignParticipationGroup,
    CampaignRequirement,
    Group,
    TelegramOutbox,
)

_TARGET_OPERATIONS = frozenset(
    {"send_campaign_card", "create_required_chat_invite", "revoke_required_chat_invite"}
)


async def has_active_campaign_block(session: object, campaign_id: UUID) -> bool:
    block_id = await session.scalar(
        select(CampaignOperationalBlock.id).where(
            CampaignOperationalBlock.campaign_id == campaign_id,
            CampaignOperationalBlock.resolved_at.is_(None),
        )
    )
    return block_id is not None


async def record_outbox_target_block(
    session: object,
    row: TelegramOutbox,
    *,
    error_code: str,
    details: str,
) -> None:
    target = await _target_identity(session, row)
    if target is None:
        return
    campaign_id, target_kind, chat_id = target
    block = await session.scalar(
        select(CampaignOperationalBlock).where(
            CampaignOperationalBlock.campaign_id == campaign_id,
            CampaignOperationalBlock.target_kind == target_kind,
            CampaignOperationalBlock.chat_id == chat_id,
            CampaignOperationalBlock.resolved_at.is_(None),
        )
    )
    now = utcnow()
    if block is not None:
        block.last_observed_at = now
        block.error_code = error_code
        block.details = details[:4000]
        return
    session.add(
        CampaignOperationalBlock(
            campaign_id=campaign_id,
            target_kind=target_kind,
            chat_id=chat_id,
            error_code=error_code,
            details=details[:4000],
            first_detected_at=now,
            last_observed_at=now,
        )
    )


async def resolve_outbox_target_block(session: object, row: TelegramOutbox) -> None:
    target = await _target_identity(session, row)
    if target is None:
        return
    campaign_id, target_kind, chat_id = target
    block = await session.scalar(
        select(CampaignOperationalBlock).where(
            CampaignOperationalBlock.campaign_id == campaign_id,
            CampaignOperationalBlock.target_kind == target_kind,
            CampaignOperationalBlock.chat_id == chat_id,
            CampaignOperationalBlock.resolved_at.is_(None),
        )
    )
    if block is not None:
        block.last_observed_at = utcnow()
        block.resolved_at = block.last_observed_at


async def project_qualification_blocks(
    session: object,
    *,
    campaign_id: UUID,
    observations: tuple[dict[str, object], ...],
) -> None:
    latest = {int(item["chat_id"]): item for item in observations}
    for chat_id, item in latest.items():
        target_kind = str(item.get("target_kind") or "requirement")
        block = await session.scalar(
            select(CampaignOperationalBlock).where(
                CampaignOperationalBlock.campaign_id == campaign_id,
                CampaignOperationalBlock.target_kind == target_kind,
                CampaignOperationalBlock.chat_id == chat_id,
                CampaignOperationalBlock.resolved_at.is_(None),
            )
        )
        if str(item["state"]) == "unknown":
            await _upsert_qualification_block(
                session,
                campaign_id=campaign_id,
                target_kind=target_kind,
                chat_id=chat_id,
                item=item,
                block=block,
            )
        elif block is not None:
            block.last_observed_at = utcnow()
            block.resolved_at = block.last_observed_at


async def _upsert_qualification_block(
    session: object,
    *,
    campaign_id: UUID,
    target_kind: str,
    chat_id: int,
    item: dict[str, object],
    block: CampaignOperationalBlock | None,
) -> None:
    now = utcnow()
    error_code = str(item.get("error_code") or "membership_unknown")
    if block is not None:
        block.last_observed_at = now
        block.error_code = error_code
        return
    session.add(
        CampaignOperationalBlock(
            campaign_id=campaign_id,
            target_kind=target_kind,
            chat_id=chat_id,
            error_code=error_code,
            first_detected_at=now,
            last_observed_at=now,
        )
    )


async def _target_identity(session: object, row: TelegramOutbox) -> tuple[UUID, str, int] | None:
    if row.operation not in _TARGET_OPERATIONS or row.aggregate_type != "campaign":
        return None
    campaign_id = UUID(row.aggregate_id)
    participation = await session.scalar(
        select(CampaignParticipationGroup)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            or_(
                Group.chat_id == row.recipient_chat_id,
                CampaignParticipationGroup.chat_id_snapshot == row.recipient_chat_id,
            ),
        )
    )
    if participation is not None:
        if row.operation == "send_campaign_card":
            participation.publication_state = (
                "unknown" if row.state is OutboxState.UNKNOWN_HOLD else "failed"
            )
        return campaign_id, "participation_group", row.recipient_chat_id
    requirement = await session.scalar(
        select(CampaignRequirement)
        .outerjoin(Group, Group.id == CampaignRequirement.target_group_id)
        .where(
            CampaignRequirement.campaign_id == campaign_id,
            or_(
                CampaignRequirement.chat_id_snapshot == row.recipient_chat_id,
                Group.chat_id == row.recipient_chat_id,
            ),
        )
    )
    if requirement is not None:
        return campaign_id, "requirement", row.recipient_chat_id
    return None
