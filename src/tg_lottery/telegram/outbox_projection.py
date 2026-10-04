from datetime import datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_lottery.application.campaign_notifications import add_cancelled_campaign_card
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.application.required_chat_links import project_required_chat_operation
from tg_lottery.application.rule_publication import project_rule_page
from tg_lottery.domain.admission import validate_admission_window
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignReferralRoute,
    Group,
    InviteLink,
    Participant,
    TelegramOutbox,
)
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.gateway_requests import invite_link_id


async def project_outbox_success(
    session: AsyncSession,
    row: TelegramOutbox,
    result: GatewayResult,
    *,
    cipher: SecretCipher,
) -> None:
    if await project_rule_page(session, row):
        return
    if await project_required_chat_operation(session, row, result, cipher=cipher):
        return
    if row.operation == "send_campaign_card":
        await _project_campaign_card(session, row)
        return
    if row.operation == "create_invite_link":
        await _project_invite_link(session, row, result, cipher=cipher)
        return
    if row.operation == "revoke_invite_link":
        link = await _required_link(session, row)
        link.revoked_at = utcnow()
        link.revoked_reason = str(row.payload.get("reason") or "explicit_remote_revoke")


async def _project_campaign_card(session: AsyncSession, row: TelegramOutbox) -> None:
    campaign = await session.get(Campaign, UUID(row.aggregate_id), with_for_update=True)
    if campaign is None:
        raise RuntimeError("published campaign disappeared")
    target = await _participation_target(session, campaign.id, row.recipient_chat_id)
    if target is not None:
        await _project_multi_group_card(session, campaign=campaign, target=target, row=row)
        return
    _project_legacy_card(session, campaign, row)


async def _participation_target(
    session: AsyncSession, campaign_id: UUID, chat_id: int
) -> CampaignParticipationGroup | None:
    return await session.scalar(
        select(CampaignParticipationGroup)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            or_(
                Group.chat_id == chat_id,
                CampaignParticipationGroup.chat_id_snapshot == chat_id,
            ),
        )
        .with_for_update()
    )


def _project_legacy_card(session: AsyncSession, campaign: Campaign, row: TelegramOutbox) -> None:
    if campaign.published_message_id == row.remote_message_id and campaign.state in {
        CampaignState.SCHEDULED,
        CampaignState.RUNNING,
    }:
        return
    if campaign.state is CampaignState.CANCELLED:
        campaign.published_message_id = row.remote_message_id
        add_cancelled_campaign_card(session, campaign, row.recipient_chat_id)
        return
    if campaign.state is not CampaignState.PUBLISHING:
        raise RuntimeError("campaign publication projection has invalid state")
    campaign.published_message_id = row.remote_message_id
    campaign.state = (
        CampaignState.RUNNING if utcnow() >= campaign.preheat_at else CampaignState.SCHEDULED
    )


async def _project_multi_group_card(
    session: AsyncSession,
    *,
    campaign: Campaign,
    target: CampaignParticipationGroup,
    row: TelegramOutbox,
) -> None:
    target.published_message_id = row.remote_message_id
    target.publication_state = "confirmed"
    target.published_at = target.published_at or utcnow()
    if target.is_primary:
        campaign.published_message_id = row.remote_message_id
    await session.flush()
    if campaign.state is CampaignState.CANCELLED:
        add_cancelled_campaign_card(
            session, campaign, row.recipient_chat_id, message_id=row.remote_message_id
        )
        return
    if campaign.state is not CampaignState.PUBLISHING:
        raise RuntimeError("campaign publication projection has invalid state")
    pending = await session.scalar(
        select(CampaignParticipationGroup.id).where(
            CampaignParticipationGroup.campaign_id == campaign.id,
            CampaignParticipationGroup.publication_state != "confirmed",
        )
    )
    if pending is None:
        try:
            validate_admission_window(
                CampaignRules.model_validate(campaign.rules),
                start_at=utcnow(),
                cutoff_at=campaign.cutoff_at,
            )
        except ValueError as exc:
            campaign.presentation = campaign.presentation | {"publication_window_error": str(exc)}
            return
        campaign.state = (
            CampaignState.RUNNING if utcnow() >= campaign.preheat_at else CampaignState.SCHEDULED
        )


async def _project_invite_link(
    session: AsyncSession,
    row: TelegramOutbox,
    result: GatewayResult,
    *,
    cipher: SecretCipher,
) -> None:
    participant_id = UUID(str(row.payload["participant_id"]))
    route_id = _route_id(row)
    existing = await _current_invite_link(session, row, participant_id, route_id=route_id)
    if existing:
        return
    if result.invite_link is None:
        raise RuntimeError("Telegram success omitted encrypted invite_link")
    await _validate_route(session, route_id, participant_id)
    link = _new_invite_link(row, result, participant_id, route_id=route_id)
    session.add(link)
    await session.flush()
    link.encrypted_url = cipher.encrypt(result.invite_link, context=f"invite-link:{link.id}")
    participant = await session.get(Participant, participant_id)
    if participant is None:
        raise RuntimeError("invite link participant disappeared")
    payload = {
        "invite_link_id": str(link.id),
        "group_title": row.payload.get("group_title"),
    }
    referral_url = _referral_url(row)
    if referral_url:
        payload["referral_url"] = referral_url
    add_outbox(
        session,
        operation="send_invite_link",
        aggregate_type="invite_link",
        aggregate_id=str(link.id),
        recipient_chat_id=participant.user_id,
        business_key=f"send_invite_link:{link.id}:{participant.user_id}",
        payload=payload,
    )


def _route_id(row: TelegramOutbox) -> UUID | None:
    raw = row.payload.get("referral_route_id")
    return UUID(str(raw)) if raw else None


async def _current_invite_link(
    session: AsyncSession,
    row: TelegramOutbox,
    participant_id: UUID,
    *,
    route_id: UUID | None,
) -> InviteLink | None:
    if route_id:
        return await session.scalar(
            select(InviteLink).where(
                InviteLink.referral_route_id == route_id,
                InviteLink.is_current.is_(True),
            )
        )
    group_id = row.payload.get("participation_group_id")
    group_clause = (
        InviteLink.participation_group_id == UUID(str(group_id))
        if group_id
        else InviteLink.participation_group_id.is_(None)
    )
    return await session.scalar(
        select(InviteLink).where(
            InviteLink.campaign_id == UUID(str(row.payload["campaign_id"])),
            InviteLink.participant_id == participant_id,
            group_clause,
            InviteLink.is_current.is_(True),
        )
    )


async def _validate_route(
    session: AsyncSession, route_id: UUID | None, participant_id: UUID
) -> None:
    if route_id is None:
        return
    route = await session.get(CampaignReferralRoute, route_id)
    if route is None or route.participant_id != participant_id:
        raise RuntimeError("invite link referral route disappeared")


def _new_invite_link(
    row: TelegramOutbox,
    result: GatewayResult,
    participant_id: UUID,
    *,
    route_id: UUID | None,
) -> InviteLink:
    secret = result.invite_link
    if secret is None:
        raise RuntimeError("Telegram success omitted encrypted invite_link")
    return InviteLink(
        campaign_id=UUID(str(row.payload["campaign_id"])),
        participant_id=participant_id,
        participation_group_id=(
            UUID(str(row.payload["participation_group_id"]))
            if row.payload.get("participation_group_id")
            else None
        ),
        referral_route_id=route_id,
        version=int(row.payload.get("link_version", 1)),
        is_current=True,
        link_hash=sha256(secret.encode()).hexdigest(),
        telegram_name=str(row.payload["name"]),
        expire_at=(
            datetime.fromisoformat(str(row.payload["expire_at"]))
            if row.payload.get("expire_at")
            else None
        ),
        remote_created_at=utcnow(),
    )


def _referral_url(row: TelegramOutbox) -> str | None:
    if not row.payload.get("referral_route_id"):
        return None
    username = str(row.payload.get("bot_username") or "").removeprefix("@")
    public_ref = str(row.payload["referral_public_ref"])
    if not username:
        raise RuntimeError("invite link projection requires bot username")
    return f"https://t.me/{username}?start=ref_{public_ref}"


async def _required_link(session: AsyncSession, row: TelegramOutbox) -> InviteLink:
    link_id = invite_link_id(row)
    link = await session.get(InviteLink, link_id, with_for_update=True) if link_id else None
    if link is None:
        raise RuntimeError("invite link projection target is missing")
    return link
