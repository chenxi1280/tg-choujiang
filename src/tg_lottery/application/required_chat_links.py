import base64
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.application.publication import add_campaign_card_outbox
from tg_lottery.application.records import add_audit, add_outbox, utcnow
from tg_lottery.infrastructure.db import Campaign, Group, Prize, TelegramOutbox
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
