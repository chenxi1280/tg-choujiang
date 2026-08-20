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
    {
        "send_campaign_card",
        "create_required_chat_invite",
        "revoke_required_chat_invite",
    }
)
QUALIFICATION_RETRY_LIMIT = 3


async def has_active_campaign_block(session: object, campaign_id: UUID) -> bool:
    block_id = await session.scalar(
        select(CampaignOperationalBlock.id).where(
            CampaignOperationalBlock.campaign_id == campaign_id,
            CampaignOperationalBlock.resolved_at.is_(None),
        )
    )
    return block_id is not None


async def has_active_target_block(
    session: object, campaign_id: UUID, *, chat_ids: tuple[int, ...]
) -> bool:
    if not chat_ids:
        return False
    block_id = await session.scalar(
        select(CampaignOperationalBlock.id).where(
            CampaignOperationalBlock.campaign_id == campaign_id,
            CampaignOperationalBlock.chat_id.in_(chat_ids),
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
    target = await _target_identity(session, row, mark_publication_failed=True)
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
            source="telegram_outbox",
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
        elif block is not None and block.source == "qualification_review":
            block.last_observed_at = utcnow()
            block.resolved_at = block.last_observed_at


async def record_qualification_target_failure(
    session: object,
    *,
    campaign_id: UUID,
    target_kind: str,
    chat_id: int,
    error_code: str,
    details: str,
) -> bool:
    block = await _active_target_block(
        session, campaign_id, target_kind=target_kind, chat_id=chat_id
    )
    now = utcnow()
    if block is None:
        block = CampaignOperationalBlock(
            campaign_id=campaign_id,
            target_kind=target_kind,
            chat_id=chat_id,
            error_code=error_code,
            source="qualification_review",
            failure_count=1,
            details=details[:4000],
            first_detected_at=now,
            last_observed_at=now,
        )
        session.add(block)
    elif block.source == "qualification_review":
        block.failure_count += 1
        block.error_code = error_code
        block.details = details[:4000]
        block.last_observed_at = now
    if block.source != "qualification_review":
        return True
    if block.failure_count >= QUALIFICATION_RETRY_LIMIT:
        block.retry_exhausted_at = block.retry_exhausted_at or now
    return block.retry_exhausted_at is not None


async def resolve_qualification_target_blocks(
    session: object, campaign_id: UUID, chat_ids: tuple[int, ...]
) -> None:
    if not chat_ids:
        return
    rows = tuple(
        await session.scalars(
            select(CampaignOperationalBlock).where(
                CampaignOperationalBlock.campaign_id == campaign_id,
                CampaignOperationalBlock.source == "qualification_review",
                CampaignOperationalBlock.chat_id.in_(chat_ids),
                CampaignOperationalBlock.resolved_at.is_(None),
            )
        )
    )
    now = utcnow()
    for row in rows:
        row.last_observed_at = now
        row.resolved_at = now


async def reset_qualification_blocks_for_retry(session: object, campaign_id: UUID) -> int:
    rows = tuple(
        await session.scalars(
            select(CampaignOperationalBlock).where(
                CampaignOperationalBlock.campaign_id == campaign_id,
                CampaignOperationalBlock.source == "qualification_review",
                CampaignOperationalBlock.resolved_at.is_(None),
            )
        )
    )
    for row in rows:
        row.failure_count = 0
        row.retry_exhausted_at = None
    return len(rows)


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
        if block.source != "qualification_review":
            return
        block.last_observed_at = now
        block.error_code = error_code
        return
    session.add(
        CampaignOperationalBlock(
            campaign_id=campaign_id,
            target_kind=target_kind,
            chat_id=chat_id,
            error_code=error_code,
            source="qualification_review",
            first_detected_at=now,
            last_observed_at=now,
        )
    )


async def _active_target_block(
    session: object, campaign_id: UUID, *, target_kind: str, chat_id: int
) -> CampaignOperationalBlock | None:
    return await session.scalar(
        select(CampaignOperationalBlock)
        .where(
            CampaignOperationalBlock.campaign_id == campaign_id,
            CampaignOperationalBlock.target_kind == target_kind,
            CampaignOperationalBlock.chat_id == chat_id,
            CampaignOperationalBlock.resolved_at.is_(None),
        )
        .with_for_update()
    )


async def _target_identity(
    session: object,
    row: TelegramOutbox,
    *,
    mark_publication_failed: bool = False,
) -> tuple[UUID, str, int] | None:
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
        if row.operation == "send_campaign_card" and mark_publication_failed:
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
