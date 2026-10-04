from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.v30_helpers import (
    FIRST,
    SECOND,
    START,
    invite_link,
    prepare_draw,
    rules_for,
    seed_v3,
    speech,
)
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.admission_qualification import SourceQualificationUnknown
from tg_lottery.application.invitation_models import JoinObservation
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import (
    AdmissionProgress,
    BaseActivityEvent,
    Participant,
    QualificationCheck,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def confirm(service, link_id, number):
    joined = START + timedelta(minutes=number)
    invitation = await service.record_joined(JoinObservation(link_id, 300 + number, joined))
    await service.begin_validation(invitation.id, observed_at=joined)
    await service.complete_validation(invitation.id, observed_at=joined, is_current_member=True)
    await service.complete_validation(invitation.id, observed_at=joined, is_current_member=True)
    return invitation


@pytest.mark.parametrize("mode", ["message", "invite"])
@pytest.mark.parametrize("bonuses", [(), ("activity",), ("invite",), ("activity", "invite")])
async def test_eight_combinations_gate_rewards_and_final_entries(session_factory, mode, bonuses):
    cid, pid = await seed_v3(session_factory, rules_for(mode, bonuses))
    service = InvitationService(partial(UnitOfWork, session_factory))
    link = await invite_link(session_factory, cid, pid)
    await speech(session_factory, count=1)
    await confirm(service, link, 1)
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert not participant.admission_qualified
        assert await SettlementService(None)._eligible_entries(session, cid) == []
    await _verify_live_query(session_factory, cid, total=0, completed=False)
    await speech(session_factory, at=START + timedelta(seconds=3), count=1, first_id=2)
    await confirm(service, link, 2)
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        expected = 1 + int("activity" in bonuses) + 2 * int("invite" in bonuses)
        assert participant.admission_qualified
        assert participant.ticket_total_cache == expected
        entries = await SettlementService(None)._eligible_entries(session, cid)
        assert [(p.id, weight) for p, weight in entries] == [(pid, expected)]
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == expected
        assert await session.scalar(select(func.count(AdmissionProgress.id))) == 1

    await _verify_live_query(session_factory, cid, total=expected, completed=True)
    await _draw_and_verify(session_factory, cid, pid, expected=expected)


async def _verify_live_query(factory, cid, *, total, completed):
    from tg_lottery.infrastructure.db import Campaign

    async with factory() as session:
        campaign = await session.get(Campaign, cid)
        public_ref = campaign.public_ref
    detail = await QueryService(factory).participant_campaign_detail(201, public_ref)
    assert detail is not None
    assert detail.base_qualified is completed
    assert f"当前有效抽奖次数: {total}" in detail.base_progress
    assert ("门槛: 已完成" if completed else "门槛: 待完成") in detail.base_progress


async def _draw_and_verify(factory, cid, pid, *, expected):
    from tg_lottery.application.drawing import DrawingService

    now = await prepare_draw(factory, cid, pid)
    uow_factory = partial(UnitOfWork, factory)
    snapshot = await SettlementService(uow_factory).settle(cid, now)
    assert snapshot.total_weight == expected
    run = await DrawingService(uow_factory).run(cid)
    replay = await DrawingService(uow_factory).run(cid)
    assert replay.id == run.id
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(TelegramOutbox).where(
                    TelegramOutbox.operation.in_(["send_draw_result", "send_winner_notice"])
                )
            )
        )
        assert {(row.operation, row.recipient_chat_id) for row in rows} == {
            ("send_draw_result", FIRST),
            ("send_draw_result", SECOND),
            ("send_winner_notice", 201),
        }
        assert len(rows) == 3


async def test_per_group_20_34_cross_date_and_one_notice_final_state(session_factory):
    rules = rules_for(
        "message",
        ("activity",),
        groups={str(FIRST): dict(daily_messages=20), str(SECOND): dict(daily_messages=34)},
    )
    _cid, pid = await seed_v3(session_factory, rules)
    await speech(session_factory, count=19)
    async with session_factory() as session:
        assert await session.scalar(select(func.count(AdmissionProgress.id))) == 0
    await speech(session_factory, count=2, first_id=20, at=START + timedelta(seconds=20))
    async with session_factory() as session:
        assert not (await session.get(Participant, pid)).admission_qualified
        assert await session.scalar(select(func.count(AdmissionProgress.id))) == 1
        notices = list(
            await session.scalars(
                select(TelegramOutbox).where(
                    TelegramOutbox.business_key.like("%message:-100123:20")
                )
            )
        )
        assert len(notices) == 1
        assert len(notices[0].payload["event_refs"]) == 2
        assert "有效抽奖次数 0" in notices[0].payload["ticket_status"]
    await speech(session_factory, chat_id=SECOND, count=35, at=START + timedelta(days=1))
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
        assert await session.scalar(select(func.count(AdmissionProgress.id))) == 2
        notice = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.business_key.like("%message:-100124:34"))
        )
        assert len(notice.payload["event_refs"]) == 2
        assert "有效抽奖次数 11" in notice.payload["ticket_status"]


async def test_invite_gate_only_link_and_confirmed_departure_preserved(session_factory):
    cid, pid = await seed_v3(session_factory, rules_for("invite"))
    participants = ParticipantService(partial(UnitOfWork, session_factory))
    await participants.request_group_invite_link(cid, 201, FIRST, bot_username="lottery_test_bot")
    async with session_factory() as session:
        assert await session.scalar(select(TelegramOutbox.operation)) == "create_invite_link"
    link = await invite_link(session_factory, cid, pid)
    service = InvitationService(partial(UnitOfWork, session_factory))
    first = await confirm(service, link, 1)
    await confirm(service, link, 2)
    await service.record_left(
        cid,
        first.invitee_user_id,
        START + timedelta(hours=1),
        participation_group_id=first.participation_group_id,
    )
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified and participant.ticket_total_cache == 1


async def test_cache_pending_uses_source_check_and_unknown_stays_unresolved(session_factory):
    cid, pid = await seed_v3(session_factory, rules_for("message"))
    async with session_factory() as session, session.begin():
        (await session.get(Participant, pid)).qualification_state = QualificationState.PENDING
    await speech(session_factory)
    async with session_factory() as session, session.begin():
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified
        assert await SettlementService(None)._eligible_entries(session, cid) == []
        session.add(
            QualificationCheck(
                participant_id=pid,
                phase="registration",
                state=QualificationState.UNKNOWN,
                observed_at=START + timedelta(hours=1),
                request_hash="b" * 64,
            )
        )
    service = ActivityService(partial(UnitOfWork, session_factory))
    with pytest.raises(SourceQualificationUnknown):
        await service.record_message(
            MessageObservation(
                FIRST, 50, 201, START + timedelta(hours=2), "来源资格仍待核实的有效发言"
            )
        )
    async with session_factory() as session:
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 2
