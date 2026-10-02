from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.invitation_models import JoinObservation
from tg_lottery.application.invitations import InvitationService
from tg_lottery.domain.enums import InvitationState, QualificationState, TicketSource
from tg_lottery.domain.rules import CampaignRules, GroupInviteRule, InviteRule
from tg_lottery.infrastructure.db import (
    CampaignParticipationGroup,
    CampaignReferralRoute,
    GroupMembershipFact,
    Invitation,
    InviteLink,
    Participant,
    ScheduledJob,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork

GROUP_CHAT_ID = -100123
INVITEE_USER_ID = 9001
SECOND_INVITER_USER_ID = 202
RETENTION_HOURS = 1
REVISIT_DELAY = timedelta(minutes=10)
SOURCE_REF = "AAAAAAAAAAAAAAAAAAAAAA"
SECOND_REF = "BBBBBBBBBBBBBBBBBBBBBB"
INVITATION_FIELDS = (
    "id",
    "campaign_id",
    "invite_link_id",
    "inviter_participant_id",
    "participation_group_id",
    "invitee_user_id",
    "state",
    "candidate_at",
    "joined_at",
    "due_at",
    "left_at",
    "validation_version",
    "validation_error",
    "final_reason",
    "source_update_id",
    "validation_observations",
)


@dataclass(frozen=True)
class ReferralCase:
    campaign_id: UUID
    participant_id: UUID
    group_id: UUID
    target_id: UUID
    route_id: UUID
    second_route_id: UUID
    link_id: UUID
    joined_at: datetime


@pytest.mark.parametrize("entry", ["same", "other_inviter", "rotated"])
@pytest.mark.parametrize("phase", ["joined_pending", "checking", "validation_unknown", "valid"])
async def test_referral_revisit_preserves_join_and_awards_original_inviter(
    session_factory, *, entry: str, phase: str
) -> None:
    case = await _seed_case(session_factory)
    service = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await _joined_invitation(service, case, phase=phase)
    before = _invitation_facts(invitation)
    route_id = await _revisit_route(session_factory, case, entry=entry)
    revisited_at = (
        case.joined_at + REVISIT_DELAY
        if phase == "joined_pending"
        else invitation.due_at + REVISIT_DELAY
    )
    revisited = await service.record_existing_member(
        route_id,
        INVITEE_USER_ID,
        revisited_at,
        display_name="受邀成员",
    )
    assert revisited is not None
    assert _invitation_facts(revisited) == before
    await _assert_original_join_facts(session_factory, case, invitation)
    if phase == "checking":
        final = await service.complete_validation(
            invitation.id, observed_at=invitation.due_at + REVISIT_DELAY, is_current_member=True
        )
    else:
        final = await service.validate(
            invitation.id, observed_at=invitation.due_at + REVISIT_DELAY, is_current_member=True
        )
    assert final.state is InvitationState.VALID
    await _assert_single_original_award(session_factory, case)


async def test_referral_visit_without_recorded_join_is_explicitly_existing_member(
    session_factory,
) -> None:
    case = await _seed_case(session_factory)
    service = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await service.record_existing_member(
        case.route_id, INVITEE_USER_ID, case.joined_at, display_name="原有成员"
    )
    assert invitation is not None
    assert invitation.state is InvitationState.INVALID
    assert invitation.final_reason == "existing_member"
    assert invitation.joined_at is None and invitation.due_at is None
    async with session_factory() as session:
        fact = await session.scalar(select(GroupMembershipFact))
        assert fact.first_source_invite_link_id is None
        assert await session.scalar(select(func.count(TicketLedger.id))) == 0
        assert await session.scalar(select(func.count(ScheduledJob.id))) == 0


async def test_referral_visit_does_not_preserve_join_with_a_different_first_source(
    session_factory,
) -> None:
    case = await _seed_case(session_factory)
    service = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await _joined_invitation(service, case, phase="joined_pending")
    async with session_factory() as session, session.begin():
        fact = await session.scalar(select(GroupMembershipFact))
        second_link = await session.scalar(
            select(InviteLink).where(InviteLink.referral_route_id == case.second_route_id)
        )
        fact.first_source_invite_link_id = second_link.id
    revisited = await service.record_existing_member(
        case.route_id, INVITEE_USER_ID, case.joined_at + REVISIT_DELAY, display_name=None
    )
    assert revisited.id == invitation.id
    assert revisited.state is InvitationState.INVALID
    assert revisited.final_reason == "existing_member"
    assert revisited.invite_link_id == case.link_id


async def _seed_case(session_factory) -> ReferralCase:
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(retention_hours=RETENTION_HOURS),
        group_invite_rules={str(GROUP_CHAT_ID): GroupInviteRule(invites_per_cycle=1)},
    )
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session, rules=rules)
        target = CampaignParticipationGroup(
            campaign_id=campaign.id,
            group_id=group.id,
            position=1,
            is_primary=True,
            chat_id_snapshot=GROUP_CHAT_ID,
            title_snapshot=group.title,
            private=False,
            permission_snapshot={"bot_admin_validated": True},
        )
        second = Participant(
            campaign_id=campaign.id,
            user_id=SECOND_INVITER_USER_ID,
            display_name="另一位邀请人",
            private_reachable=True,
            qualification_state=QualificationState.ELIGIBLE,
            registered_revision=1,
        )
        session.add_all((target, second))
        await session.flush()
        route, link = await _add_route(
            session, participant, target=target, public_ref=SOURCE_REF, cutoff=campaign.cutoff_at
        )
        second_route, _ = await _add_route(
            session, second, target=target, public_ref=SECOND_REF, cutoff=campaign.cutoff_at
        )
        return ReferralCase(
            campaign.id,
            participant.id,
            group.id,
            target.id,
            route.id,
            second_route.id,
            link.id,
            datetime.now(UTC),
        )


async def _add_route(session, participant, *, target, public_ref, cutoff):
    route = CampaignReferralRoute(
        public_ref=public_ref,
        campaign_id=participant.campaign_id,
        participant_id=participant.id,
        participation_group_id=target.id,
        bonus_join_deadline=cutoff - timedelta(hours=RETENTION_HOURS),
    )
    session.add(route)
    await session.flush()
    link = _route_link(route, version=1)
    session.add(link)
    await session.flush()
    return route, link


def _route_link(route, *, version: int) -> InviteLink:
    return InviteLink(
        campaign_id=route.campaign_id,
        participant_id=route.participant_id,
        participation_group_id=route.participation_group_id,
        referral_route_id=route.id,
        version=version,
        link_hash=sha256(f"{route.id}:{version}".encode()).hexdigest(),
        encrypted_url=b"encrypted",
        telegram_name=f"lottery_revisit_v{version}",
    )


async def _joined_invitation(service, case: ReferralCase, *, phase: str) -> Invitation:
    invitation = await service.record_joined(
        JoinObservation(
            case.link_id,
            INVITEE_USER_ID,
            case.joined_at,
            chat_id=GROUP_CHAT_ID,
            display_name="受邀成员",
        )
    )
    assert invitation is not None
    if phase == "checking":
        await service.begin_validation(invitation.id, observed_at=invitation.due_at)
        return await service._current(invitation.id)
    if phase in {"validation_unknown", "valid"}:
        return await service.validate(
            invitation.id,
            observed_at=invitation.due_at,
            is_current_member=True if phase == "valid" else None,
        )
    return invitation


async def _revisit_route(session_factory, case: ReferralCase, *, entry: str) -> UUID:
    if entry == "other_inviter":
        return case.second_route_id
    if entry == "rotated":
        async with session_factory() as session, session.begin():
            link = await session.get(InviteLink, case.link_id)
            link.is_current = False
            await session.flush()
            route = await session.get(CampaignReferralRoute, case.route_id)
            session.add(_route_link(route, version=2))
    return case.route_id


def _invitation_facts(invitation: Invitation) -> tuple[object, ...]:
    return tuple(getattr(invitation, name) for name in INVITATION_FIELDS)


async def _assert_original_join_facts(session_factory, case, invitation) -> None:
    async with session_factory() as session:
        fact = await session.scalar(select(GroupMembershipFact))
        assert fact.group_id == case.group_id
        assert fact.first_source_invite_link_id == case.link_id
        assert fact.first_observed_member_at == case.joined_at
        jobs = tuple(await session.scalars(select(ScheduledJob)))
        assert len(jobs) == 1
        assert jobs[0].aggregate_id == str(invitation.id)
        assert jobs[0].run_at == invitation.due_at
        assert jobs[0].business_key == (
            f"validate_invitation:{invitation.id}:{invitation.validation_version}"
        )
        assert await session.scalar(select(func.count(Invitation.id))) == 1


async def _assert_single_original_award(session_factory, case: ReferralCase) -> None:
    async with session_factory() as session:
        rows = tuple(
            await session.scalars(
                select(TicketLedger).where(TicketLedger.source_type == TicketSource.INVITATION)
            )
        )
        assert len(rows) == 1
        assert rows[0].campaign_id == case.campaign_id
        assert rows[0].participant_id == case.participant_id
        assert rows[0].participation_group_id == case.target_id
        assert rows[0].delta == 1
