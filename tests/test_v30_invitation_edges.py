from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.test_v30_admission_integration import confirm
from tests.v30_helpers import FIRST, SECOND, START, invite_link, rules_for, seed_v3
from tg_lottery.application.invitation_models import JoinObservation
from tg_lottery.application.invitations import InvitationService
from tg_lottery.domain.enums import InvitationState
from tg_lottery.domain.rules import GroupInviteRule
from tg_lottery.infrastructure.db import AdmissionProgress, Invitation, Participant, TicketLedger
from tg_lottery.infrastructure.db.uow import UnitOfWork


@pytest.mark.parametrize("gate_count,cycle", [(2, 3), (3, 1)])
async def test_gate_count_and_unlimited_award_cycles_are_independent(
    session_factory, gate_count, cycle
):
    rules = rules_for(
        "invite", ("invite",), groups={str(FIRST): {"required_valid_invites": gate_count}}
    )
    rules = rules.model_copy(
        update={"group_invite_rules": {str(FIRST): GroupInviteRule(invites_per_cycle=cycle)}}
    )
    cid, pid = await seed_v3(session_factory, rules)
    link = await invite_link(session_factory, cid, pid)
    service = InvitationService(partial(UnitOfWork, session_factory))
    for number in range(1, 22):
        await confirm(service, link, number)
        async with session_factory() as session:
            participant = await session.get(Participant, pid)
            assert participant.admission_qualified == (number >= gate_count)
            assert participant.ticket_total_cache == 1 + number // cycle
    async with session_factory() as session:
        assert await session.scalar(select(func.count(AdmissionProgress.id))) == 1
        assert (
            await session.scalar(
                select(func.count(Invitation.id)).where(Invitation.state == InvitationState.VALID)
            )
            == 21
        )
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 1 + 21 // cycle


async def test_same_invitee_is_counted_once_in_each_corresponding_group(session_factory):
    rules = rules_for(
        "invite",
        groups={
            str(FIRST): {"required_valid_invites": 1},
            str(SECOND): {"required_valid_invites": 1},
        },
    )
    cid, pid = await seed_v3(session_factory, rules)
    service = InvitationService(partial(UnitOfWork, session_factory))
    first_link = await invite_link(session_factory, cid, pid)
    second_link = await invite_link(session_factory, cid, pid, chat_id=SECOND)
    await confirm(service, first_link, 1)
    async with session_factory() as session:
        assert not (await session.get(Participant, pid)).admission_qualified
    second = await confirm(service, second_link, 1)
    replay = await service.record_joined(
        JoinObservation(
            second_link, second.invitee_user_id, START + timedelta(hours=1), chat_id=SECOND
        )
    )
    assert replay.id == second.id
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
        assert await session.scalar(select(func.count(Invitation.id))) == 2
        assert await session.scalar(select(func.count(AdmissionProgress.id))) == 2
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 1
