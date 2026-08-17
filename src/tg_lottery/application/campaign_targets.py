from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.authorization import require_group_admin
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignDraft
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignRequirement,
    Group,
)

MULTI_GROUP_CONTRACT = "v2.1"
_TERMINAL_STATES = frozenset({CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED})


@dataclass(frozen=True)
class QualificationTarget:
    kind: str
    record_id: UUID
    chat_id: int
    title: str
    chat_type: str
    private: bool
    public_join_url: str | None
    encrypted_join_url: bytes | None
    revoked_at: datetime | None
    secret_context: str


async def resolve_draft_groups(
    session: object, draft: CampaignDraft, actor_user_id: int
) -> tuple[Group, ...]:
    chat_ids = draft.all_participation_group_ids
    rows = tuple(await session.scalars(select(Group).where(Group.chat_id.in_(chat_ids))))
    by_chat = {group.chat_id: group for group in rows}
    missing = [chat_id for chat_id in chat_ids if chat_id not in by_chat]
    if missing:
        raise ValueError(f"participation groups are not registered: {missing}")
    ordered = tuple(by_chat[chat_id] for chat_id in chat_ids)
    for group in ordered:
        await require_group_admin(session, group_id=group.id, user_id=actor_user_id)
    return ordered


async def add_campaign_targets(
    session: object,
    *,
    campaign: Campaign,
    draft: CampaignDraft,
    groups: tuple[Group, ...],
) -> None:
    records = _participation_records(draft.presentation)
    participation_ids = {group.chat_id for group in groups}
    requirement_ids = {int(item["id"]) for item in _requirements(draft.presentation)}
    overlap = participation_ids.intersection(requirement_ids)
    if overlap:
        raise ValueError(f"participation groups conflict with requirements: {sorted(overlap)}")
    for position, group in enumerate(groups, start=1):
        presentation = records.get(group.chat_id, {})
        session.add(_participation_row(campaign, group, presentation, position=position))
    for position, item in enumerate(_requirements(draft.presentation), start=1):
        target_group = await _requirement_group(session, campaign, item)
        session.add(_requirement_row(campaign, item, position=position, target_group=target_group))


async def require_campaign_admin(session: object, *, campaign_id: UUID, user_id: int) -> None:
    group_ids = tuple(
        await session.scalars(
            select(CampaignParticipationGroup.group_id).where(
                CampaignParticipationGroup.campaign_id == campaign_id
            )
        )
    )
    if not group_ids:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError("campaign not found")
        group_ids = (campaign.group_id,)
    for group_id in group_ids:
        await require_group_admin(session, group_id=group_id, user_id=user_id)


async def participation_rows(
    session: object, campaign_id: UUID, *, confirmed_only: bool = False
) -> tuple[CampaignParticipationGroup, ...]:
    statement = select(CampaignParticipationGroup).where(
        CampaignParticipationGroup.campaign_id == campaign_id
    )
    if confirmed_only:
        statement = statement.where(CampaignParticipationGroup.publication_state == "confirmed")
    rows = await session.scalars(statement.order_by(CampaignParticipationGroup.position))
    return tuple(rows)


async def participation_chat_ids(
    session: object, campaign_id: UUID, *, confirmed_only: bool = False
) -> tuple[int, ...]:
    statement = (
        select(Group.chat_id)
        .join(CampaignParticipationGroup, CampaignParticipationGroup.group_id == Group.id)
        .where(CampaignParticipationGroup.campaign_id == campaign_id)
    )
    if confirmed_only:
        statement = statement.where(CampaignParticipationGroup.publication_state == "confirmed")
    rows = await session.scalars(statement.order_by(CampaignParticipationGroup.position))
    values = tuple(rows)
    if values:
        return values
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise ValueError("campaign not found")
    if campaign.contract_version == MULTI_GROUP_CONTRACT:
        raise RuntimeError("multi-group campaign has no participation targets")
    group = await session.get(Group, campaign.group_id)
    return (group.chat_id,) if group is not None else ()


async def qualification_targets(
    session: object, campaign: Campaign
) -> tuple[QualificationTarget, ...]:
    groups = tuple(
        await session.execute(
            select(CampaignParticipationGroup, Group.chat_id)
            .join(Group, Group.id == CampaignParticipationGroup.group_id)
            .where(CampaignParticipationGroup.campaign_id == campaign.id)
            .order_by(CampaignParticipationGroup.position)
        )
    )
    requirements = tuple(
        await session.execute(
            select(CampaignRequirement, Group.chat_id)
            .outerjoin(Group, Group.id == CampaignRequirement.target_group_id)
            .where(CampaignRequirement.campaign_id == campaign.id)
            .order_by(CampaignRequirement.position)
        )
    )
    if campaign.contract_version == MULTI_GROUP_CONTRACT and not groups:
        raise RuntimeError("multi-group campaign has no participation targets")
    return tuple(_group_target(campaign, item, chat_id) for item, chat_id in groups) + tuple(
        _requirement_target(campaign, item, chat_id) for item, chat_id in requirements
    )


async def release_participation_groups(
    session: object, campaign: Campaign, released_at: datetime
) -> None:
    if campaign.state not in _TERMINAL_STATES:
        return
    for row in await participation_rows(session, campaign.id):
        row.released_at = row.released_at or released_at


def _participation_row(
    campaign: Campaign,
    group: Group,
    item: dict[str, Any],
    *,
    position: int,
) -> CampaignParticipationGroup:
    username = item.get("username")
    return CampaignParticipationGroup(
        campaign_id=campaign.id,
        group_id=group.id,
        position=position,
        is_primary=group.id == campaign.group_id,
        chat_id_snapshot=group.chat_id,
        title_snapshot=group.title,
        username_snapshot=str(username) if username else None,
        private=bool(item.get("private", False)),
        public_join_url=str(item["join_url"]) if item.get("join_url") else None,
        permission_snapshot={
            "creator_admin_validated": bool(item.get("creator_admin_validated")),
            "bot_admin_validated": bool(item.get("bot_admin_validated")),
        },
    )


def _requirement_row(
    campaign: Campaign,
    item: dict[str, Any],
    *,
    position: int,
    target_group: Group | None,
) -> CampaignRequirement:
    chat_id = int(item["id"])
    return CampaignRequirement(
        campaign_id=campaign.id,
        target_group_id=target_group.id if target_group else None,
        chat_id_snapshot=chat_id,
        position=position,
        chat_type=str(item.get("type", "group")),
        title=str(item.get("title") or "未命名群组/频道"),
        username=str(item["username"]) if item.get("username") else None,
        private=bool(item.get("private", True)),
        public_join_url=str(item["join_url"]) if item.get("join_url") else None,
        message_threshold=_message_threshold(campaign, chat_id),
        resolved_at=campaign.created_at,
    )


async def _requirement_group(
    session: object, campaign: Campaign, item: dict[str, Any]
) -> Group | None:
    if item.get("type") == "channel":
        return None
    chat_id = int(item["id"])
    group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
    if group is not None:
        return group
    group = Group(
        chat_id=chat_id,
        title=str(item.get("title") or "未命名群组"),
        timezone=campaign.timezone,
    )
    session.add(group)
    await session.flush()
    return group


def _participation_records(presentation: dict[str, Any]) -> dict[int, dict[str, Any]]:
    items = presentation.get("participation_groups") or []
    records = {int(item["id"]): item for item in items if isinstance(item, dict)}
    main = presentation.get("main_chat")
    if isinstance(main, dict):
        records.setdefault(int(main["id"]), main)
    return records


def _requirements(presentation: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    items = presentation.get("required_chats") or []
    return tuple(item for item in items if isinstance(item, dict))


def _message_threshold(campaign: Campaign, chat_id: int) -> int | None:
    thresholds = campaign.rules.get("message_thresholds") or {}
    value = thresholds.get(str(chat_id))
    return int(value) if value else None


def _group_target(
    campaign: Campaign, row: CampaignParticipationGroup, chat_id: int
) -> QualificationTarget:
    return QualificationTarget(
        kind="participation_group",
        record_id=row.id,
        chat_id=chat_id,
        title=row.title_snapshot,
        chat_type="group",
        private=row.private,
        public_join_url=row.public_join_url,
        encrypted_join_url=row.encrypted_join_url,
        revoked_at=row.revoked_at,
        secret_context=_qualification_secret_context(campaign, "participation_group", row),
    )


def _requirement_target(
    campaign: Campaign,
    row: CampaignRequirement,
    current_chat_id: int | None,
) -> QualificationTarget:
    return QualificationTarget(
        kind="requirement",
        record_id=row.id,
        chat_id=current_chat_id or row.chat_id_snapshot,
        title=row.title,
        chat_type=row.chat_type,
        private=row.private,
        public_join_url=row.public_join_url,
        encrypted_join_url=row.encrypted_join_url,
        revoked_at=row.revoked_at,
        secret_context=_qualification_secret_context(campaign, "requirement", row),
    )


def _qualification_secret_context(
    campaign: Campaign,
    kind: str,
    row: CampaignParticipationGroup | CampaignRequirement,
) -> str:
    if campaign.contract_version == MULTI_GROUP_CONTRACT:
        return f"campaign-target:{campaign.id}:{kind}:{row.id}"
    return f"required-chat:{campaign.id}:{row.chat_id_snapshot}"
