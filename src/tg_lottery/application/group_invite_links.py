from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.invitation_models import campaign_accepts_attribution
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.enums import OutboxState, QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.public_refs import (
    is_neutral_invite_link_name,
    is_referral_public_ref,
)
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignOperationalBlock,
    CampaignParticipationGroup,
    CampaignReferralRoute,
    Group,
    InviteLink,
    Participant,
    TelegramOutbox,
)


@dataclass(frozen=True)
class GroupInviteLinkRequest:
    campaign_id: UUID
    user_id: int
    chat_id: int
    bot_username: str


async def request_group_invite_link(
    uow_factory: UnitOfWorkFactory,
    *,
    request: GroupInviteLinkRequest,
    name_factory: Callable[[], str],
    ref_factory: Callable[[], str],
) -> None:
    if not request.bot_username.removeprefix("@").strip():
        raise ValueError("bot username is required for a stable referral route")
    async with uow_factory() as uow:
        campaign = await uow.session.get(Campaign, request.campaign_id)
        participant = await _eligible_participant(uow.session, request)
        target = await _participation_group(uow.session, request)
        _validate_request(campaign, participant, target, chat_id=request.chat_id)
        await _require_target_available(uow.session, request)
        group = await uow.session.get(Group, target.group_id)
        if group is None:
            raise ValueError("participation group is missing")
        route = await _ensure_referral_route(
            uow.session,
            campaign=campaign,
            participant=participant,
            target=target,
            ref_factory=ref_factory,
        )
        await _queue_link_request(
            uow.session,
            campaign=campaign,
            participant=participant,
            target=target,
            group=group,
            route=route,
            bot_username=request.bot_username,
            name_factory=name_factory,
        )


def _validate_request(
    campaign: Campaign | None,
    participant: Participant | None,
    target: CampaignParticipationGroup | None,
    *,
    chat_id: int,
) -> None:
    if campaign is None or participant is None or target is None:
        raise UserFacingError("仅合格参与者可获取本活动群邀请链接")
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.invite_rule_for(chat_id) is None:
        raise UserFacingError("该群未开启邀请加成")
    deadline = campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours)
    now = utcnow()
    if now > deadline:
        raise UserFacingError("本期最晚计奖入群时间已过\n仍可通过普通入口加入群聊")
    if not campaign_accepts_attribution(campaign, now):
        raise UserFacingError("活动当前不接受新增邀请加成入口")


async def _require_target_available(session: object, request: GroupInviteLinkRequest) -> None:
    blocked = await session.scalar(
        select(CampaignOperationalBlock.id).where(
            CampaignOperationalBlock.campaign_id == request.campaign_id,
            CampaignOperationalBlock.chat_id == request.chat_id,
            CampaignOperationalBlock.resolved_at.is_(None),
        )
    )
    if blocked:
        raise UserFacingError("该群机器人权限异常, 请联系创建者将机器人设为管理员后重试")


async def _queue_link_request(
    session: object,
    *,
    campaign: Campaign,
    participant: Participant,
    target: CampaignParticipationGroup,
    group: Group,
    route: CampaignReferralRoute,
    bot_username: str,
    name_factory: Callable[[], str],
) -> None:
    version = await _next_link_version(session, route.id)
    if version is None:
        return
    business_key = f"invite_link:{route.id}:{version}"
    if await _existing_link_request(session, business_key):
        return
    invite_name = name_factory()
    if not is_neutral_invite_link_name(invite_name):
        raise ValueError("invite link name is invalid")
    add_outbox(
        session,
        operation="create_invite_link",
        aggregate_type="participant",
        aggregate_id=str(participant.id),
        recipient_chat_id=group.chat_id,
        business_key=business_key,
        payload=_outbox_payload(
            campaign,
            participant,
            target,
            route=route,
            link_version=version,
            bot_username=bot_username,
            invite_name=invite_name,
        ),
    )


async def _next_link_version(session: object, route_id: UUID) -> int | None:
    link = await session.scalar(
        select(InviteLink)
        .where(
            InviteLink.referral_route_id == route_id,
            InviteLink.is_current.is_(True),
        )
        .with_for_update()
    )
    if link is not None and link.revoked_at is None:
        return None
    if link is not None:
        link.is_current = False
    latest = await session.scalar(
        select(func.max(InviteLink.version)).where(InviteLink.referral_route_id == route_id)
    )
    return int(latest or 0) + 1


async def _existing_link_request(session: object, business_key: str) -> bool:
    outbox = await session.scalar(
        select(TelegramOutbox).where(TelegramOutbox.business_key == business_key).with_for_update()
    )
    if outbox is None:
        return False
    if outbox.state is OutboxState.PRE_ACCEPT_FAILED:
        _reset_failed_request(outbox)
    return True


def _reset_failed_request(outbox: TelegramOutbox) -> None:
    outbox.state = OutboxState.PENDING
    outbox.run_at = utcnow()
    outbox.lease_owner = None
    outbox.lease_until = None
    outbox.gateway_started_at = None


def _outbox_payload(
    campaign: Campaign,
    participant: Participant,
    target: CampaignParticipationGroup,
    *,
    route: CampaignReferralRoute,
    link_version: int,
    bot_username: str,
    invite_name: str,
) -> dict[str, object]:
    return {
        "campaign_id": str(campaign.id),
        "participant_id": str(participant.id),
        "participation_group_id": str(target.id),
        "referral_route_id": str(route.id),
        "referral_public_ref": route.public_ref,
        "link_version": link_version,
        "group_title": target.title_snapshot,
        "persistent": True,
        "bot_username": bot_username,
        "name": invite_name,
    }


async def _ensure_referral_route(
    session: object,
    *,
    campaign: Campaign,
    participant: Participant,
    target: CampaignParticipationGroup,
    ref_factory: Callable[[], str],
) -> CampaignReferralRoute:
    route = await session.scalar(
        select(CampaignReferralRoute)
        .where(
            CampaignReferralRoute.campaign_id == campaign.id,
            CampaignReferralRoute.participant_id == participant.id,
            CampaignReferralRoute.participation_group_id == target.id,
        )
        .with_for_update()
    )
    if route is not None:
        return route
    public_ref = ref_factory()
    if not is_referral_public_ref(public_ref):
        raise ValueError("referral public reference is invalid")
    rules = CampaignRules.model_validate(campaign.rules)
    route = CampaignReferralRoute(
        public_ref=public_ref,
        campaign_id=campaign.id,
        participant_id=participant.id,
        participation_group_id=target.id,
        bonus_join_deadline=campaign.cutoff_at - timedelta(hours=rules.invite.retention_hours),
    )
    session.add(route)
    await session.flush()
    return route


async def _eligible_participant(
    session: object, request: GroupInviteLinkRequest
) -> Participant | None:
    return await session.scalar(
        select(Participant)
        .where(
            Participant.campaign_id == request.campaign_id,
            Participant.user_id == request.user_id,
            Participant.qualification_state == QualificationState.ELIGIBLE,
        )
        .with_for_update()
    )


async def _participation_group(
    session: object, request: GroupInviteLinkRequest
) -> CampaignParticipationGroup | None:
    return await session.scalar(
        select(CampaignParticipationGroup).where(
            CampaignParticipationGroup.campaign_id == request.campaign_id,
            CampaignParticipationGroup.chat_id_snapshot == request.chat_id,
        )
    )
