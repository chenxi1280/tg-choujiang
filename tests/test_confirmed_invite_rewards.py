from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.test_v22_group_bonus_integration import (
    _seed_invitation_campaign,
    _validate_group_invites,
)
from tg_lottery.application.invitation_models import JoinObservation
from tg_lottery.application.invitation_rewards import add_invite_bonus
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import InvitationState, TicketSource
from tg_lottery.domain.rules import CampaignRules, GroupInviteRule, InviteRule
from tg_lottery.infrastructure.db import (
    Campaign,
    Invitation,
    InviteLink,
    Participant,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.outbox_projection import project_outbox_success


async def seed(session_factory):
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(retention_hours=1),
        group_invite_rules={
            "-100123": GroupInviteRule(invites_per_cycle=2),
            "-100124": GroupInviteRule(invites_per_cycle=1),
        },
    )
    links, participant_id, campaign_id = await _seed_invitation_campaign(
        session_factory, rules, now
    )
    return SimpleNamespace(
        now=now,
        links=links,
        participant_id=participant_id,
        campaign_id=campaign_id,
        service=InvitationService(partial(UnitOfWork, session_factory)),
    )


async def validate_users(context, user_ids, *, group_index=0):
    await _validate_group_invites(
        context.service,
        link_id=context.links[group_index],
        chat_id=(-100123, -100124)[group_index],
        user_ids=user_ids,
        now=context.now,
    )


async def leave(context, user_id, session_factory):
    async with session_factory() as session:
        link = await session.get(InviteLink, context.links[0])
        group_id = link.participation_group_id
    await context.service.record_left(
        context.campaign_id,
        user_id,
        context.now + timedelta(hours=2),
        participation_group_id=group_id,
    )
    return group_id


async def test_confirmed_departures_remain_counted_for_future_awards_and_other_groups(
    session_factory,
):
    context = await seed(session_factory)
    await validate_users(context, (1001, 1002))
    await validate_users(context, (2001,), group_index=1)
    await leave(context, 1001, session_factory)
    await leave(context, 1001, session_factory)
    await assert_progress(session_factory, context, count=2, reward=1, total=2)
    await validate_users(context, (1003,))
    await assert_progress(session_factory, context, count=3, reward=1, total=2)
    await validate_users(context, (1004,))
    await assert_progress(session_factory, context, count=4, reward=2, total=3)
    async with session_factory() as session:
        rows = list(await session.scalars(select(TicketLedger)))
        notices = list(await session.scalars(select(TelegramOutbox)))
        assert [row.delta for row in rows] == [1, 1, 1]
        assert all(row.source_type is TicketSource.INVITATION for row in rows)
        assert all("扣减" not in row.payload.get("text", "") for row in notices)


async def assert_progress(session_factory, context, *, count, reward, total):
    queries = QueryService(session_factory)
    progress = await queries.group_bonus_progress(context.campaign_id, 201)
    by_chat = {item.chat_id: item for item in progress.invitations}
    assert by_chat[-100123].valid_invites == count
    assert by_chat[-100123].awarded_tickets == reward
    assert by_chat[-100124].valid_invites == 1
    stats = await queries.campaign_stats(context.campaign_id)
    assert stats.valid_invitations == count + 1
    async with session_factory() as session:
        assert (await session.get(Participant, context.participant_id)).ticket_total_cache == total


@pytest.mark.parametrize(
    "path",
    ["member_rejoin", "link_rejoin", "plain_link_rejoin", "join_request", "duplicate_validation"],
)
async def test_confirmed_rejoin_paths_preserve_award_without_new_validation(session_factory, path):
    context = await seed(session_factory)
    await validate_users(context, (1001, 1002))
    group_id = await leave(context, 1001, session_factory)
    async with session_factory() as session:
        invitation = await session.scalar(
            select(Invitation).where(Invitation.invitee_user_id == 1001)
        )
        version, due_at = invitation.validation_version, invitation.due_at
    observation = JoinObservation(
        context.links[0],
        1001,
        context.now + timedelta(hours=3),
        chat_id=-100123,
        was_already_member=path != "plain_link_rejoin",
    )
    result = await replay(context, invitation, observation, path=path, group_id=group_id)
    assert result.state is InvitationState.VALID
    assert result.validation_version == version
    assert result.due_at == due_at
    async with session_factory() as session:
        ledger = list(await session.scalars(select(TicketLedger)))
        assert [row.delta for row in ledger] == [1]
        assert (await session.get(Participant, context.participant_id)).ticket_total_cache == 1


async def replay(context, invitation, observation, *, path, group_id):
    if path == "member_rejoin":
        return await context.service.record_rejoined(
            context.campaign_id, 1001, observation.event_at, participation_group_id=group_id
        )
    if path in {"link_rejoin", "plain_link_rejoin"}:
        return await context.service.record_joined(observation)
    if path == "join_request":
        return await context.service.record_join_request(observation)
    return await context.service.validate(
        invitation.id, observed_at=observation.event_at, is_current_member=False
    )


async def test_link_expiry_and_revocation_do_not_touch_confirmed_or_stashed_rewards(
    session_factory,
):
    context = await seed(session_factory)
    await validate_users(context, (1001, 1002))
    async with session_factory() as session, session.begin():
        participant = await session.get(Participant, context.participant_id)
        participant.base_qualified = False
        link = await session.get(InviteLink, context.links[0])
        link.expire_at = context.now - timedelta(minutes=1)
        request = SimpleNamespace(
            operation="revoke_invite_link", payload={"invite_link_id": str(link.id)}
        )
        await project_outbox_success(session, request, GatewayResult(remote_result={}), cipher=None)
    async with session_factory() as session:
        link = await session.get(InviteLink, context.links[0])
        assert link.revoked_at is not None
        assert (await session.get(Participant, context.participant_id)).ticket_total_cache == 1
        assert len(list(await session.scalars(select(TicketLedger)))) == 1
        assert all(
            row.state is InvitationState.VALID for row in await session.scalars(select(Invitation))
        )


async def test_leave_before_first_confirmation_does_not_earn_reward(session_factory):
    context = await seed(session_factory)
    observation = JoinObservation(context.links[0], 1001, context.now, chat_id=-100123)
    invitation = await context.service.record_joined(observation)
    await leave(context, 1001, session_factory)
    result = await context.service.validate(
        invitation.id, observed_at=context.now + timedelta(hours=3), is_current_member=False
    )
    assert result.state is InvitationState.LEFT_BEFORE_VALID
    async with session_factory() as session:
        assert not list(await session.scalars(select(TicketLedger)))


async def test_negative_invitation_award_is_rejected_without_ledger_or_notice(session_factory):
    context = await seed(session_factory)
    await validate_users(context, (1001, 1002))
    with pytest.raises(ValueError, match="confirmed bonuses cannot be revoked"):
        async with session_factory() as session, session.begin():
            campaign = await session.get(Campaign, context.campaign_id)
            invitation = await session.scalar(select(Invitation))
            await add_invite_bonus(
                session,
                campaign=campaign,
                invitation=invitation,
                reward=-1,
                business_key="invalid-deduction",
                occurred_at=context.now,
            )
    async with session_factory() as session:
        assert [row.delta for row in await session.scalars(select(TicketLedger))] == [1]
        notices = list(await session.scalars(select(TelegramOutbox)))
        durable = [
            row for row in notices if not row.business_key.startswith("progress_incomplete:")
        ]
        incomplete = [row for row in notices if row.business_key.startswith("progress_incomplete:")]
        assert len(durable) == 1
        assert len(incomplete) == 1
        assert incomplete[0].payload["delete_after_seconds"] == 15
        assert "delete_after_seconds" not in durable[0].payload
        assert "notice:invalid-deduction" not in {row.business_key for row in notices}


async def test_inviter_departure_changes_qualification_but_preserves_earned_bonus(session_factory):
    from tg_lottery.application.participants import ParticipantService
    from tg_lottery.domain.enums import QualificationState

    context = await seed(session_factory)
    await validate_users(context, (1001, 1002))
    service = ParticipantService(partial(UnitOfWork, session_factory))
    await service.mark_membership_ineligible(
        campaign_id=context.campaign_id,
        user_id=201,
        chat_id=-100123,
        observed_at=context.now + timedelta(hours=2),
    )
    async with session_factory() as session:
        participant = await session.get(Participant, context.participant_id)
        assert participant.qualification_state is QualificationState.INELIGIBLE
        assert participant.ticket_total_cache == 1
        assert [row.delta for row in await session.scalars(select(TicketLedger))] == [1]
