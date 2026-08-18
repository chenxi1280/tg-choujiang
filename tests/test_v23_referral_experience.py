import base64
from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState, InvitationState, QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules, GroupInviteRule, InviteRule
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignReferralRoute,
    GroupMembershipFact,
    InviteLink,
    Participant,
    TelegramOutbox,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.outbox_projection import project_outbox_success

FIRST_REF = "AAAAAAAAAAAAAAAAAAAAAA"
SECOND_REF = "BBBBBBBBBBBBBBBBBBBBBB"
BOT_USERNAME = "lottery_test_bot"


def _cipher() -> SecretCipher:
    key = base64.urlsafe_b64encode(b"v23-test-key-material-32-bytes!!").decode()
    return SecretCipher(key)


async def _requested_route(session_factory):
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(retention_hours=1),
        group_invite_rules={"-100123": GroupInviteRule(invites_per_cycle=2)},
    )
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session, rules=rules)
        session.add(_target(campaign))
        await session.flush()
        campaign_id, participant_id = campaign.id, participant.id
    service = ParticipantService(
        partial(UnitOfWork, session_factory),
        invite_name_factory=lambda: "lottery_v23_route",
        referral_ref_factory=lambda: FIRST_REF,
    )
    await service.request_group_invite_link(campaign_id, 201, -100123, bot_username=BOT_USERNAME)
    return service, campaign_id, participant_id


async def _project_latest_link(session_factory, cipher: SecretCipher, invite_url: str) -> None:
    async with session_factory() as session, session.begin():
        outboxes = tuple(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.operation == "create_invite_link")
            )
        )
        assert outboxes
        outbox = max(outboxes, key=lambda row: int(row.payload.get("link_version", 1)))
        await project_outbox_success(
            session,
            outbox,
            GatewayResult(remote_result={"ok": True}, invite_link=invite_url),
            cipher=cipher,
        )


def _target(campaign: Campaign, *, title: str = "测试群") -> CampaignParticipationGroup:
    return CampaignParticipationGroup(
        campaign_id=campaign.id,
        group_id=campaign.group_id,
        position=1,
        is_primary=True,
        chat_id_snapshot=-100123,
        title_snapshot=title,
        private=False,
        public_join_url="https://t.me/test_group",
        permission_snapshot={"bot_admin_validated": True},
    )


def _second_campaign(group_id, rules: CampaignRules, now: datetime) -> Campaign:
    return Campaign(
        group_id=group_id,
        name="第二个同群活动",
        state=CampaignState.RUNNING,
        timezone=rules.timezone,
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(days=1),
        draw_at=now + timedelta(days=1, minutes=10),
        rules=rules.model_dump(mode="json"),
        presentation={
            "participation_groups": [
                {
                    "id": -100123,
                    "title": "测试群",
                    "join_url": "https://t.me/test_group",
                }
            ]
        },
        created_by=101,
        contract_version="v2.3",
    )


async def test_same_group_supports_multiple_active_campaigns(session_factory) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules()
    async with session_factory() as session, session.begin():
        group, first, first_participant = await seed_campaign(session, rules=rules)
        first.contract_version = "v2.3"
        second = _second_campaign(group.id, rules, now)
        session.add(second)
        await session.flush()
        second_participant = Participant(
            campaign_id=second.id,
            user_id=201,
            display_name="同一用户的第二次参与",
            private_reachable=True,
            qualification_state=QualificationState.ELIGIBLE,
            registered_revision=1,
        )
        first_target, second_target = _target(first), _target(second)
        session.add_all((second_participant, first_target, second_target))
        await session.flush()
        session.add_all(
            (
                _route(FIRST_REF, first, first_participant, target=first_target),
                _route(SECOND_REF, second, second_participant, target=second_target),
            )
        )

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(CampaignParticipationGroup.id)).where(
                CampaignParticipationGroup.group_id == group.id,
                CampaignParticipationGroup.released_at.is_(None),
            )
        )
        routes = tuple(await session.scalars(select(CampaignReferralRoute)))
    assert count == 2
    assert {route.public_ref for route in routes} == {FIRST_REF, SECOND_REF}
    assert len({route.campaign_id for route in routes}) == 2
    active = await QueryService(session_factory).active_campaigns_by_chat(-100123)
    assert len(active) == 2


async def test_personal_route_is_stable_and_underlying_link_stays_persistent(
    session_factory,
) -> None:
    service, campaign_id, participant_id = await _requested_route(session_factory)
    await service.request_group_invite_link(campaign_id, 201, -100123, bot_username=BOT_USERNAME)
    cipher = _cipher()
    await _project_latest_link(session_factory, cipher, "https://t.me/+v23-link")

    async with session_factory() as session:
        routes = tuple(await session.scalars(select(CampaignReferralRoute)))
        links = tuple(await session.scalars(select(InviteLink)))
        delivery = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_invite_link")
        )
    assert len(routes) == 1
    assert routes[0].public_ref == FIRST_REF
    assert routes[0].participant_id == participant_id
    assert len(links) == 1
    assert links[0].expire_at is None
    assert delivery.payload["referral_url"] == (
        f"https://t.me/{BOT_USERNAME}?start=ref_{FIRST_REF}"
    )
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.cutoff_at = datetime.now(UTC) - timedelta(minutes=1)
        campaign.state = CampaignState.SETTLING
    landing = await QueryService(session_factory).referral_landing_by_reference(FIRST_REF)
    assert landing is not None
    assert landing.link is not None
    assert (
        cipher.decrypt(landing.link.encrypted_url, context=f"invite-link:{landing.link.id}")
        == "https://t.me/+v23-link"
    )


async def test_revoked_underlying_link_rotates_without_changing_public_route(
    session_factory,
) -> None:
    service, campaign_id, _ = await _requested_route(session_factory)
    cipher = _cipher()
    await _project_latest_link(session_factory, cipher, "https://t.me/+version-one")
    async with session_factory() as session, session.begin():
        current = await session.scalar(select(InviteLink).where(InviteLink.is_current.is_(True)))
        current.revoked_at = datetime.now(UTC)
        current.revoked_reason = "explicit_remote_revoke"

    await service.request_group_invite_link(campaign_id, 201, -100123, bot_username=BOT_USERNAME)
    await _project_latest_link(session_factory, cipher, "https://t.me/+version-two")

    async with session_factory() as session:
        links = tuple(await session.scalars(select(InviteLink).order_by(InviteLink.version)))
        routes = tuple(await session.scalars(select(CampaignReferralRoute)))
    assert [link.version for link in links] == [1, 2]
    assert [link.is_current for link in links] == [False, True]
    assert len(routes) == 1 and routes[0].public_ref == FIRST_REF
    landing = await QueryService(session_factory).referral_landing_by_reference(FIRST_REF)
    assert landing is not None and landing.link is not None
    assert (
        cipher.decrypt(landing.link.encrypted_url, context=f"invite-link:{landing.link.id}")
        == "https://t.me/+version-two"
    )


async def test_cancelled_campaign_cannot_create_or_rotate_referral_link(session_factory) -> None:
    service, campaign_id, _ = await _requested_route(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.state = CampaignState.CANCELLED

    with pytest.raises(UserFacingError, match="不接受新增"):
        await service.request_group_invite_link(
            campaign_id, 201, -100123, bot_username=BOT_USERNAME
        )

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(TelegramOutbox.id)).where(
                TelegramOutbox.operation == "create_invite_link"
            )
        )
    assert count == 1


async def test_group_history_blocks_cross_campaign_re_attribution(session_factory) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(retention_hours=1),
        group_invite_rules={"-100123": GroupInviteRule(invites_per_cycle=1)},
    )
    async with session_factory() as session, session.begin():
        group, first, first_participant = await seed_campaign(session, rules=rules)
        first_target = _target(first)
        second = _second_campaign(group.id, rules, now)
        session.add_all((first_target, second))
        await session.flush()
        second_participant = Participant(
            campaign_id=second.id,
            user_id=202,
            display_name="第二位邀请人",
            private_reachable=True,
            qualification_state=QualificationState.ELIGIBLE,
            registered_revision=1,
        )
        second_target = _target(second)
        session.add_all((second_participant, second_target))
        await session.flush()
        first_link = _link(first, first_participant, first_target, suffix="first")
        second_link = _link(second, second_participant, second_target, suffix="second")
        session.add_all((first_link, second_link))
        await session.flush()
        first_link_id = first_link.id
        second_link_id = second_link.id
        second_ref = second.public_ref

    service = InvitationService(partial(UnitOfWork, session_factory))
    first_record = await service.record_joined(_join(first_link_id, now, display_name="被邀请人"))
    second_record = await service.record_joined(
        _join(second_link_id, now + timedelta(minutes=1), display_name="被邀请人")
    )

    assert first_record is not None
    assert second_record is not None
    assert second_record.state is InvitationState.INVALID
    assert second_record.final_reason == "existing_member"
    async with session_factory() as session:
        fact_count = await session.scalar(select(func.count(GroupMembershipFact.id)))
    assert fact_count == 1
    records = await QueryService(session_factory).campaign_invitation_records(202, second_ref)
    assert records is not None
    assert records.invalid == 1
    assert records.rows[0].group_title == "测试群"
    assert records.rows[0].final_reason == "existing_member"


def _link(
    campaign: Campaign,
    participant: Participant,
    target: CampaignParticipationGroup,
    *,
    suffix: str,
) -> InviteLink:
    url = f"https://t.me/+{suffix}"
    return InviteLink(
        campaign_id=campaign.id,
        participant_id=participant.id,
        participation_group_id=target.id,
        link_hash=sha256(url.encode()).hexdigest(),
        encrypted_url=b"encrypted",
        telegram_name=f"lottery_{suffix}",
        expire_at=None,
    )


def _route(
    public_ref: str,
    campaign: Campaign,
    participant: Participant,
    *,
    target: CampaignParticipationGroup,
) -> CampaignReferralRoute:
    return CampaignReferralRoute(
        public_ref=public_ref,
        campaign_id=campaign.id,
        participant_id=participant.id,
        participation_group_id=target.id,
        bonus_join_deadline=campaign.cutoff_at - timedelta(hours=1),
    )


def _join(link_id, observed_at: datetime, *, display_name: str):
    from tg_lottery.application.invitation_models import JoinObservation

    return JoinObservation(
        link_id,
        9001,
        observed_at,
        chat_id=-100123,
        display_name=display_name,
    )
