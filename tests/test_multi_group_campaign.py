from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.campaign_targets import (
    MULTI_GROUP_CONTRACT,
    qualification_targets,
)
from tg_lottery.application.campaigns import CampaignService, PrizeSpec
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.application.participants import ParticipantService, RegistrationRequest
from tg_lottery.domain.enums import QualificationState, TicketSource
from tg_lottery.domain.errors import (
    AuthorizationDenied,
    InvitationValidationDeferred,
    UserFacingError,
)
from tg_lottery.domain.rules import CampaignDraft, CampaignRules, InviteRule
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignOperationalBlock,
    CampaignParticipationGroup,
    CampaignRequirement,
    Group,
    GroupAdmin,
    InviteLink,
    ScheduledJob,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork

ACTOR_ID = 101
PRIMARY_CHAT_ID = -100701
SECONDARY_CHAT_ID = -100702
REQUIREMENT_CHAT_ID = -100703
MIGRATED_SECONDARY_CHAT_ID = -100712
MIGRATED_REQUIREMENT_CHAT_ID = -100713


def test_campaign_draft_rejects_target_role_overlap() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="conflict with requirements"):
        CampaignDraft(
            group_id=PRIMARY_CHAT_ID,
            participation_group_ids=(PRIMARY_CHAT_ID, SECONDARY_CHAT_ID),
            name="目标角色冲突",
            preheat_at=now,
            cutoff_at=now + timedelta(hours=1),
            draw_at=now + timedelta(hours=2),
            presentation={
                "required_chats": [
                    {"id": SECONDARY_CHAT_ID, "title": "重复目标", "type": "supergroup"}
                ]
            },
        )


async def test_multi_group_campaign_requires_admin_intersection_and_fans_out(
    session_factory,
) -> None:
    groups = await _seed_participation_groups(session_factory)
    service = CampaignService(partial(UnitOfWork, session_factory))
    draft = _multi_group_draft(datetime.now(UTC))
    campaign = await _create_with_admin_intersection(
        session_factory,
        service=service,
        draft=draft,
        secondary_group=groups[1],
    )
    await _publish_with_admin_intersection(
        session_factory,
        service=service,
        campaign=campaign,
        secondary_group=groups[1],
    )
    await _assert_multi_group_projection(session_factory, campaign.id)


async def _create_with_admin_intersection(
    session_factory,
    *,
    service: CampaignService,
    draft: CampaignDraft,
    secondary_group: Group,
) -> Campaign:
    with pytest.raises(AuthorizationDenied):
        await service.create_draft(draft, _prizes(), ACTOR_ID)
    async with session_factory() as session, session.begin():
        session.add(
            GroupAdmin(
                group_id=secondary_group.id,
                user_id=ACTOR_ID,
                source="administrator",
                active=True,
            )
        )
    return await service.create_draft(draft, _prizes(), ACTOR_ID)


async def _publish_with_admin_intersection(
    session_factory,
    *,
    service: CampaignService,
    campaign: Campaign,
    secondary_group: Group,
) -> None:
    async with session_factory() as session, session.begin():
        secondary_admin = await session.scalar(
            select(GroupAdmin).where(GroupAdmin.group_id == secondary_group.id)
        )
        secondary_admin.active = False
    with pytest.raises(AuthorizationDenied):
        await service.publish(campaign.id, ACTOR_ID, "lottery_test_bot")
    async with session_factory() as session, session.begin():
        secondary_admin = await session.scalar(
            select(GroupAdmin).where(GroupAdmin.group_id == secondary_group.id)
        )
        secondary_admin.active = True
    await service.publish(campaign.id, ACTOR_ID, "lottery_test_bot")


async def _assert_multi_group_projection(session_factory, campaign_id: UUID) -> None:
    async with session_factory() as session:
        stored = await session.get(Campaign, campaign_id)
        participation = tuple(
            await session.scalars(
                select(CampaignParticipationGroup)
                .where(CampaignParticipationGroup.campaign_id == campaign_id)
                .order_by(CampaignParticipationGroup.position)
            )
        )
        requirements = tuple(
            await session.scalars(
                select(CampaignRequirement).where(CampaignRequirement.campaign_id == campaign_id)
            )
        )
        targets = await qualification_targets(session, stored)
        outboxes = tuple(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.aggregate_id == str(campaign_id))
            )
        )
        jobs = tuple(
            await session.scalars(
                select(ScheduledJob).where(ScheduledJob.aggregate_id == str(campaign_id))
            )
        )
    assert stored.contract_version == MULTI_GROUP_CONTRACT
    assert [row.chat_id_snapshot for row in participation] == [
        PRIMARY_CHAT_ID,
        SECONDARY_CHAT_ID,
    ]
    assert [row.is_primary for row in participation] == [True, False]
    assert [row.chat_id_snapshot for row in requirements] == [REQUIREMENT_CHAT_ID]
    assert [(target.kind, target.chat_id) for target in targets] == [
        ("participation_group", PRIMARY_CHAT_ID),
        ("participation_group", SECONDARY_CHAT_ID),
        ("requirement", REQUIREMENT_CHAT_ID),
    ]
    assert {row.recipient_chat_id for row in outboxes} == {
        PRIMARY_CHAT_ID,
        SECONDARY_CHAT_ID,
    }
    assert {row.operation for row in outboxes} == {"send_campaign_card"}
    assert len(jobs) == 6


async def test_ratio_invite_bonus_awards_each_n_confirmed_invites_and_retains(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    campaign_id, participant_id, link_id = await _seed_ratio_campaign(session_factory, now)
    service = InvitationService(partial(UnitOfWork, session_factory))
    totals = await _validate_ratio_invites(
        session_factory,
        service=service,
        link_id=link_id,
        participant_id=participant_id,
        now=now,
    )
    await service.record_left(campaign_id, 904, now + timedelta(hours=3))
    ledger = await _participant_ledger(session_factory, participant_id)
    assert totals == [0, 1, 1, 2]
    assert [row.delta for row in ledger] == [1, 1]
    assert all(row.source_type is TicketSource.INVITATION for row in ledger)
    assert sum(row.delta for row in ledger) == 2


async def _seed_ratio_campaign(session_factory, now: datetime) -> tuple[UUID, UUID, UUID]:
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(
            mode="ratio",
            retention_hours=1,
            valid_invites_per_ticket=2,
            max_bonus_tickets=2,
        ),
    )
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session, rules=rules)
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"ratio-invite-link").hexdigest(),
            telegram_name="lot_ratio_test",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        campaign_id = campaign.id
        participant_id = participant.id
        link_id = link.id
    return campaign_id, participant_id, link_id


async def _validate_ratio_invites(
    session_factory,
    *,
    service: InvitationService,
    link_id: UUID,
    participant_id: UUID,
    now: datetime,
) -> list[int]:
    totals = []
    for offset, user_id in enumerate((901, 902, 903, 904)):
        joined_at = now + timedelta(minutes=offset)
        invitation = await service.record_joined(JoinObservation(link_id, user_id, joined_at))
        assert invitation is not None
        await service.validate(
            invitation.id,
            observed_at=joined_at + timedelta(hours=1),
            is_current_member=True,
        )
        async with session_factory() as session:
            totals.append(
                int(
                    await session.scalar(
                        select(func.coalesce(func.sum(TicketLedger.delta), 0)).where(
                            TicketLedger.participant_id == participant_id
                        )
                    )
                    or 0
                )
            )
    return totals


async def _participant_ledger(session_factory, participant_id: UUID) -> tuple[TicketLedger, ...]:
    async with session_factory() as session:
        return tuple(
            await session.scalars(
                select(TicketLedger)
                .where(TicketLedger.participant_id == participant_id)
                .order_by(TicketLedger.occurred_at)
            )
        )


async def test_private_target_publication_uses_current_migrated_chat_ids(
    session_factory,
) -> None:
    groups = await _seed_participation_groups(session_factory)
    async with session_factory() as session, session.begin():
        session.add(
            GroupAdmin(
                group_id=groups[1].id,
                user_id=ACTOR_ID,
                source="administrator",
                active=True,
            )
        )
    service = CampaignService(partial(UnitOfWork, session_factory))
    campaign = await service.create_draft(
        _multi_group_draft(datetime.now(UTC), private=True), _prizes(), ACTOR_ID
    )
    async with session_factory() as session, session.begin():
        secondary = await session.get(Group, groups[1].id)
        requirement = await session.scalar(
            select(CampaignRequirement).where(CampaignRequirement.campaign_id == campaign.id)
        )
        assert secondary is not None
        assert requirement is not None
        assert requirement.target_group_id is not None
        requirement_group = await session.get(Group, requirement.target_group_id)
        assert requirement_group is not None
        secondary.chat_id = MIGRATED_SECONDARY_CHAT_ID
        requirement_group.chat_id = MIGRATED_REQUIREMENT_CHAT_ID

    await service.publish(campaign.id, ACTOR_ID, "lottery_test_bot")

    async with session_factory() as session:
        recipients = set(
            await session.scalars(
                select(TelegramOutbox.recipient_chat_id).where(
                    TelegramOutbox.aggregate_id == str(campaign.id)
                )
            )
        )
    assert recipients == {
        MIGRATED_SECONDARY_CHAT_ID,
        MIGRATED_REQUIREMENT_CHAT_ID,
    }


async def test_operational_block_stops_new_registration_but_allows_reverification(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session, rules=CampaignRules(invite=InviteRule(retention_hours=1))
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"blocked-invite-link").hexdigest(),
            telegram_name="lot_blocked_test",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        session.add(
            CampaignOperationalBlock(
                campaign_id=campaign.id,
                target_kind="participation_group",
                chat_id=PRIMARY_CHAT_ID,
                error_code="TelegramForbiddenError",
                first_detected_at=now,
                last_observed_at=now,
            )
        )
        await session.flush()
        campaign_id = campaign.id
        link_id = link.id

    service = ParticipantService(partial(UnitOfWork, session_factory))
    existing = await service.begin_registration(_registration_request(campaign_id, 201, now))
    with pytest.raises(UserFacingError, match="等待 Telegram 权限恢复"):
        await service.begin_registration(_registration_request(campaign_id, 202, now))
    invitations = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await invitations.record_joined(JoinObservation(link_id, 905, now))
    assert invitation is not None
    with pytest.raises(InvitationValidationDeferred, match="operational block"):
        await invitations.begin_validation(invitation.id, observed_at=now + timedelta(hours=1))

    assert existing.state is QualificationState.PENDING
    assert existing.requires_observation is True


async def _seed_participation_groups(session_factory) -> tuple[Group, Group]:
    async with session_factory() as session, session.begin():
        primary = Group(chat_id=PRIMARY_CHAT_ID, title="主参与群", timezone="Asia/Shanghai")
        secondary = Group(chat_id=SECONDARY_CHAT_ID, title="第二参与群", timezone="Asia/Shanghai")
        session.add_all((primary, secondary))
        await session.flush()
        session.add(
            GroupAdmin(
                group_id=primary.id,
                user_id=ACTOR_ID,
                source="owner",
                active=True,
            )
        )
        return primary, secondary


def _multi_group_draft(now: datetime, *, private: bool = False) -> CampaignDraft:
    participation = [
        {"id": PRIMARY_CHAT_ID, "title": "主参与群", "type": "supergroup", "private": False},
        {
            "id": SECONDARY_CHAT_ID,
            "title": "第二参与群",
            "type": "supergroup",
            "private": private,
        },
    ]
    requirements = [
        {
            "id": REQUIREMENT_CHAT_ID,
            "title": "准入群",
            "type": "supergroup",
            "private": private,
        }
    ]
    return CampaignDraft(
        group_id=PRIMARY_CHAT_ID,
        participation_group_ids=(PRIMARY_CHAT_ID, SECONDARY_CHAT_ID),
        name="多群 AND 活动",
        preheat_at=now + timedelta(minutes=5),
        cutoff_at=now + timedelta(days=1),
        draw_at=now + timedelta(days=1, minutes=10),
        rules=CampaignRules(required_chat_ids=(REQUIREMENT_CHAT_ID,)),
        presentation={
            "main_chat": participation[0],
            "participation_groups": participation,
            "required_chats": requirements,
        },
    )


def _prizes() -> tuple[PrizeSpec, ...]:
    return (PrizeSpec("一等奖", 1, "联系管理员"),)


def _registration_request(
    campaign_id: UUID, user_id: int, observed_at: datetime
) -> RegistrationRequest:
    return RegistrationRequest(
        campaign_id=campaign_id,
        user_id=user_id,
        username=f"user{user_id}",
        display_name=f"用户 {user_id}",
        observed_at=observed_at,
    )
