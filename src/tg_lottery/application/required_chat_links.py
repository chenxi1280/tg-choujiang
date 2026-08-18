import base64
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.application.campaign_targets import uses_multi_group_contract
from tg_lottery.application.publication import add_campaign_card_outbox
from tg_lottery.application.records import add_audit, add_outbox, utcnow
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignRequirement,
    Group,
    Prize,
    TelegramOutbox,
)
from tg_lottery.telegram.crypto import SecretCipher


class RequiredInviteResult(Protocol):
    invite_link: str | None


async def project_required_chat_invite(
    session: AsyncSession,
    row: TelegramOutbox,
    result: RequiredInviteResult,
    *,
    cipher: SecretCipher,
) -> None:
    campaign = await _campaign(session, row)
    chat_id = int(row.payload["chat_id"])
    if uses_multi_group_contract(campaign):
        await _project_target_invite(
            session,
            campaign=campaign,
            chat_id=chat_id,
            result=result,
            cipher=cipher,
        )
        await _ensure_campaign_card(session, campaign, str(row.payload["bot_username"]))
        return
    conditions = _conditions(campaign.presentation)
    current = _condition(conditions, chat_id)
    if result.invite_link is None and not current.get("join_url_encrypted"):
        raise RuntimeError("Telegram success omitted required chat invite link")
    if not current.get("join_url_encrypted"):
        encrypted = cipher.encrypt(
            str(result.invite_link), context=_secret_context(campaign.id, chat_id)
        )
        current["join_url_encrypted"] = base64.urlsafe_b64encode(encrypted).decode()
        current["invite_created_at"] = utcnow().isoformat()
        campaign.presentation = _replace_conditions(campaign.presentation, conditions)
        add_audit(
            session,
            actor_user_id=None,
            action="campaign.required_chat_invite_created",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            details={"chat_id": chat_id},
        )
    await _ensure_campaign_card(session, campaign, str(row.payload["bot_username"]))


async def project_required_chat_revoke(session: AsyncSession, row: TelegramOutbox) -> None:
    campaign = await _campaign(session, row)
    chat_id = int(row.payload["chat_id"])
    if uses_multi_group_contract(campaign):
        target = await _target_row(session, campaign.id, chat_id)
        target.revoked_at = target.revoked_at or utcnow()
        target.encrypted_join_url = None
        return
    conditions = _conditions(campaign.presentation)
    current = _condition(conditions, chat_id)
    if current.get("invite_revoked_at"):
        return
    current["invite_revoked_at"] = utcnow().isoformat()
    campaign.presentation = _replace_conditions(campaign.presentation, conditions)
    add_audit(
        session,
        actor_user_id=None,
        action="campaign.required_chat_invite_revoked",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        details={"chat_id": chat_id},
    )


async def project_required_chat_operation(
    session: AsyncSession,
    row: TelegramOutbox,
    result: RequiredInviteResult,
    *,
    cipher: SecretCipher,
) -> bool:
    if row.operation == "create_required_chat_invite":
        await project_required_chat_invite(session, row, result, cipher=cipher)
        return True
    if row.operation == "revoke_required_chat_invite":
        await project_required_chat_revoke(session, row)
        return True
    return False


async def add_required_chat_revoke_outboxes(session: AsyncSession, campaign: Campaign) -> None:
    if uses_multi_group_contract(campaign):
        await _add_target_revoke_outboxes(session, campaign)
        return
    for chat in _conditions(campaign.presentation):
        if not chat.get("join_url_encrypted") or chat.get("invite_revoked_at"):
            continue
        chat_id = int(chat["id"])
        business_key = f"revoke_required_chat_invite:{campaign.id}:{chat_id}"
        existing = await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
        )
        if existing:
            continue
        add_outbox(
            session,
            operation="revoke_required_chat_invite",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=chat_id,
            business_key=business_key,
            payload={
                "campaign_id": str(campaign.id),
                "chat_id": chat_id,
                "join_url_encrypted": str(chat["join_url_encrypted"]),
            },
        )


def decrypt_required_chat_url(
    cipher: SecretCipher, campaign_id: UUID, chat: dict[str, object]
) -> str:
    encoded = chat.get("join_url_encrypted")
    if not encoded:
        raise ValueError("required chat invite link is not ready")
    encrypted = base64.urlsafe_b64decode(str(encoded).encode())
    return cipher.decrypt(encrypted, context=_secret_context(campaign_id, int(chat["id"])))


async def _ensure_campaign_card(
    session: AsyncSession, campaign: Campaign, bot_username: str
) -> None:
    if uses_multi_group_contract(campaign):
        await _ensure_multi_group_cards(session, campaign, bot_username)
        return
    if any(
        item.get("private") and not item.get("join_url_encrypted")
        for item in _conditions(campaign.presentation)
    ):
        return
    business_key = f"publish:{campaign.id}:{campaign.config_revision}"
    existing = await session.scalar(
        select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
    )
    if existing:
        return
    group = await session.get(Group, campaign.group_id)
    if group is None:
        raise RuntimeError("campaign group disappeared before publication")
    prizes = tuple(
        await session.scalars(
            select(Prize).where(Prize.campaign_id == campaign.id).order_by(Prize.position)
        )
    )
    add_campaign_card_outbox(
        session,
        campaign=campaign,
        recipient_chat_id=group.chat_id,
        bot_username=bot_username,
        prizes=prizes,
    )


async def _campaign(session: AsyncSession, row: TelegramOutbox) -> Campaign:
    campaign = await session.get(Campaign, UUID(row.aggregate_id), with_for_update=True)
    if campaign is None:
        raise RuntimeError("required chat campaign disappeared")
    return campaign


def _conditions(presentation: dict[str, object]) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    main = presentation.get("main_chat")
    if isinstance(main, dict):
        values.append(dict(main))
    extras = presentation.get("required_chats") or []
    values.extend(dict(item) for item in extras if isinstance(item, dict))
    return values


def _condition(conditions: list[dict[str, object]], chat_id: int) -> dict[str, object]:
    for item in conditions:
        if int(item["id"]) == chat_id:
            return item
    raise RuntimeError("required chat projection target is missing")


def _replace_conditions(
    presentation: dict[str, object], conditions: list[dict[str, object]]
) -> dict[str, object]:
    main = conditions[0] if presentation.get("main_chat") else None
    extras = conditions[1:] if main else conditions
    updated = dict(presentation) | {"required_chats": extras}
    return updated | ({"main_chat": main} if main else {})


def _secret_context(campaign_id: UUID, chat_id: int) -> str:
    return f"required-chat:{campaign_id}:{chat_id}"


async def _project_target_invite(
    session: AsyncSession,
    *,
    campaign: Campaign,
    chat_id: int,
    result: RequiredInviteResult,
    cipher: SecretCipher,
) -> None:
    target = await _target_row(session, campaign.id, chat_id)
    if result.invite_link is None and target.encrypted_join_url is None:
        raise RuntimeError("Telegram success omitted campaign target invite link")
    if target.encrypted_join_url is not None:
        return
    link = str(result.invite_link)
    target.encrypted_join_url = cipher.encrypt(
        link, context=_target_secret_context(campaign.id, target)
    )
    target.join_url_hash = sha256(link.encode()).hexdigest()
    target.invite_created_at = utcnow()
    await session.flush()
    add_audit(
        session,
        actor_user_id=None,
        action="campaign.target_invite_created",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        details={"chat_id": chat_id, "target_kind": _target_kind(target)},
    )


async def _target_row(
    session: AsyncSession, campaign_id: UUID, chat_id: int
) -> CampaignParticipationGroup | CampaignRequirement:
    participation = await session.scalar(
        select(CampaignParticipationGroup)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            or_(
                CampaignParticipationGroup.chat_id_snapshot == chat_id,
                Group.chat_id == chat_id,
            ),
        )
    )
    requirement = await session.scalar(
        select(CampaignRequirement)
        .outerjoin(Group, Group.id == CampaignRequirement.target_group_id)
        .where(
            CampaignRequirement.campaign_id == campaign_id,
            or_(
                CampaignRequirement.chat_id_snapshot == chat_id,
                Group.chat_id == chat_id,
            ),
        )
    )
    if participation is not None and requirement is not None:
        raise RuntimeError("campaign target roles overlap")
    if participation is None and requirement is None:
        raise RuntimeError("campaign target projection is missing")
    return participation if participation is not None else requirement


async def _ensure_multi_group_cards(
    session: AsyncSession, campaign: Campaign, bot_username: str
) -> None:
    if await _private_target_without_link(session, campaign.id):
        return
    prizes = tuple(
        await session.scalars(
            select(Prize).where(Prize.campaign_id == campaign.id).order_by(Prize.position)
        )
    )
    rows = tuple(
        await session.execute(
            select(CampaignParticipationGroup, Group.chat_id)
            .join(Group, Group.id == CampaignParticipationGroup.group_id)
            .where(CampaignParticipationGroup.campaign_id == campaign.id)
            .order_by(CampaignParticipationGroup.position)
        )
    )
    if not rows:
        raise RuntimeError("campaign has no participation groups")
    for _, chat_id in rows:
        add_campaign_card_outbox(
            session,
            campaign=campaign,
            recipient_chat_id=chat_id,
            bot_username=bot_username,
            prizes=prizes,
        )


async def _private_target_without_link(session: AsyncSession, campaign_id: UUID) -> bool:
    participation = await session.scalar(
        select(CampaignParticipationGroup.id).where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            CampaignParticipationGroup.private.is_(True),
            CampaignParticipationGroup.encrypted_join_url.is_(None),
        )
    )
    requirement = await session.scalar(
        select(CampaignRequirement.id).where(
            CampaignRequirement.campaign_id == campaign_id,
            CampaignRequirement.private.is_(True),
            CampaignRequirement.encrypted_join_url.is_(None),
        )
    )
    return participation is not None or requirement is not None


async def _add_target_revoke_outboxes(session: AsyncSession, campaign: Campaign) -> None:
    participation = await _encrypted_targets(session, CampaignParticipationGroup, campaign.id)
    requirements = await _encrypted_targets(session, CampaignRequirement, campaign.id)
    targets = (*participation, *requirements)
    for target in targets:
        chat_id = await _current_target_chat_id(session, target)
        add_outbox(
            session,
            operation="revoke_required_chat_invite",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=chat_id,
            business_key=f"revoke_campaign_target:{campaign.id}:{chat_id}",
            payload={
                "campaign_id": str(campaign.id),
                "chat_id": chat_id,
                "join_url_encrypted": base64.urlsafe_b64encode(target.encrypted_join_url).decode(),
                "secret_context": _target_secret_context(campaign.id, target),
            },
        )


async def _current_target_chat_id(
    session: AsyncSession, target: CampaignParticipationGroup | CampaignRequirement
) -> int:
    group_id = (
        target.group_id
        if isinstance(target, CampaignParticipationGroup)
        else target.target_group_id
    )
    group = await session.get(Group, group_id) if group_id is not None else None
    return group.chat_id if group is not None else target.chat_id_snapshot


async def _encrypted_targets(
    session: AsyncSession, model: type, campaign_id: UUID
) -> tuple[CampaignParticipationGroup | CampaignRequirement, ...]:
    rows = await session.scalars(
        select(model).where(
            model.campaign_id == campaign_id,
            model.encrypted_join_url.is_not(None),
            model.revoked_at.is_(None),
        )
    )
    return tuple(rows)


def decrypt_campaign_target_url(
    cipher: SecretCipher,
    campaign_id: UUID,
    target: CampaignParticipationGroup | CampaignRequirement,
) -> str:
    if target.encrypted_join_url is None or target.revoked_at is not None:
        raise ValueError("campaign target invite link is not available")
    return cipher.decrypt(
        target.encrypted_join_url,
        context=_target_secret_context(campaign_id, target),
    )


def _target_secret_context(
    campaign_id: UUID, target: CampaignParticipationGroup | CampaignRequirement
) -> str:
    return f"campaign-target:{campaign_id}:{_target_kind(target)}:{target.id}"


def _target_kind(target: CampaignParticipationGroup | CampaignRequirement) -> str:
    if isinstance(target, CampaignParticipationGroup):
        return "participation_group"
    return "requirement"
