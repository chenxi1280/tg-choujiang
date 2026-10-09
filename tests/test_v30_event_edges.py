import asyncio
from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.test_v30_admission_integration import confirm
from tests.v30_helpers import FIRST, SECOND, START, invite_link, rules_for, seed_v3, speech
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.groups import GroupService
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.admission import InviteAdmission, InviteAdmissionRule
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules, GroupInviteRule, InviteRule
from tg_lottery.infrastructure.db import (
    BaseActivityEvent,
    Campaign,
    Group,
    Participant,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_same_second_watermark_and_prior_source_are_rejected(session_factory):
    _cid, pid = await seed_v3(session_factory, rules_for("message"))
    service = ActivityService(partial(UnitOfWork, session_factory))
    for update, message_id in [(99, 1), (100, 2), (101, 3), (102, 4)]:
        with bind_update_context(1, update):
            await service.record_message(
                MessageObservation(FIRST, message_id, 201, START, f"来源消息序号{message_id}")
            )
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(BaseActivityEvent.id)).where(BaseActivityEvent.accepted.is_(True))
            )
            == 2
        )
        assert (await session.get(Participant, pid)).admission_qualified
        rejected = list(
            await session.scalars(
                select(BaseActivityEvent.rejection_reason).where(
                    BaseActivityEvent.accepted.is_(False)
                )
            )
        )
        assert rejected == ["before_counting_start", "before_counting_start"]


async def test_midnight_uses_source_date_and_completed_gate_stays_qualified(session_factory):
    _cid, pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    midnight = START.replace(hour=16)
    await speech(session_factory, count=1, at=midnight - timedelta(seconds=2))
    await speech(session_factory, count=1, at=midnight, first_id=2)
    async with session_factory() as session:
        assert not (await session.get(Participant, pid)).admission_qualified
    await speech(session_factory, count=1, at=midnight + timedelta(seconds=3), first_id=3)
    await speech(session_factory, count=2, at=midnight + timedelta(days=2), first_id=4)
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 3


async def test_group_upgrade_keeps_gate_and_rules_route(session_factory):
    _cid, pid = await seed_v3(session_factory, rules_for("message"))
    await speech(session_factory, count=1)
    upgraded = -100999
    async with session_factory() as session, session.begin():
        group = await session.scalar(select(Group).where(Group.chat_id == FIRST))
        group.chat_id = upgraded
    await speech(
        session_factory, chat_id=upgraded, count=1, first_id=1, at=START + timedelta(seconds=3)
    )
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 2


@pytest.mark.parametrize("text", ["/start", " ", None])
async def test_invalid_text_does_not_complete_gate(session_factory, text):
    _cid, pid = await seed_v3(session_factory, rules_for("message"))
    service = ActivityService(partial(UnitOfWork, session_factory))
    await service.record_message(
        MessageObservation(FIRST, 1, 201, START + timedelta(seconds=1), text)
    )
    await speech(session_factory, count=1, first_id=2, at=START + timedelta(seconds=3))
    async with session_factory() as session:
        assert not (await session.get(Participant, pid)).admission_qualified


async def test_global_total_deduplicates_non_consecutive_replays(session_factory):
    service = MessageStatsService(partial(UnitOfWork, session_factory))
    first = MessageObservation(FIRST, 1, 201, START, "第一条累计消息内容")
    second = MessageObservation(FIRST, 2, 201, START, "第二条累计消息内容")
    for observation in (first, second, first, second):
        await service.record(observation)
    await service.record(MessageObservation(SECOND, 1, 201, START, "另外群第一条消息内容"))
    assert await service.totals(201, (FIRST, SECOND)) == {FIRST: 2, SECOND: 1}


async def test_invite_after_group_upgrade_combines_gate_and_bonus_once(session_factory):
    cid, pid = await seed_v3(session_factory, rules_for("invite", ("invite",)))
    link_id = await invite_link(session_factory, cid, pid)
    service = InvitationService(partial(UnitOfWork, session_factory))
    await confirm(service, link_id, 1)
    upgraded = -100999
    async with session_factory() as session, session.begin():
        group = await session.scalar(select(Group).where(Group.chat_id == FIRST))
        group.chat_id = upgraded
    from tg_lottery.application.invitation_models import JoinObservation

    at = START + timedelta(minutes=2)
    invitation = await service.record_joined(JoinObservation(link_id, 302, at, chat_id=upgraded))
    await service.begin_validation(invitation.id, observed_at=at)
    await service.complete_validation(invitation.id, observed_at=at, is_current_member=True)
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified and participant.ticket_total_cache == 3
        notices = list(
            await session.scalars(
                select(TelegramOutbox).where(
                    TelegramOutbox.business_key.like(f"%invitation:{invitation.id}%")
                )
            )
        )
        assert len(notices) == 1 and notices[0].recipient_chat_id == upgraded
        assert len(notices[0].payload["event_refs"]) == 3
        assert "有效抽奖次数 3" in notices[0].payload["ticket_status"]


@pytest.mark.parametrize(
    "terminal", [CampaignState.CANCELLED, CampaignState.PAUSED, CampaignState.DRAWN]
)
async def test_cancel_between_routing_and_campaign_lock_does_not_accept_message(
    session_factory, terminal
):
    cid, _ = await seed_v3(session_factory, rules_for("message", ("activity",)))
    service = ActivityService(partial(UnitOfWork, session_factory))
    async with session_factory() as session, session.begin():
        routed = await session.get(Campaign, cid)
        async with session_factory() as writer, writer.begin():
            (await writer.get(Campaign, cid)).state = terminal
        result = await service._record_for_campaign(
            session,
            campaign=routed,
            main_chat_id=FIRST,
            observation=MessageObservation(
                FIRST, 1, 201, START + timedelta(seconds=1), "取消并发时进入的发言"
            ),
        )
        assert result is None
    async with session_factory() as session:
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 0
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 1


async def test_incomplete_progress_is_silent_after_group_migration(session_factory):
    await seed_v3(session_factory, rules_for("message", ("activity",)))
    upgraded = -100999
    await GroupService(partial(UnitOfWork, session_factory)).migrate_group(FIRST, upgraded)
    await speech(session_factory, chat_id=upgraded, count=1)
    async with session_factory() as session:
        rows = list(
            await session.scalars(
                select(TelegramOutbox).where(
                    TelegramOutbox.business_key.like("progress_incomplete:%")
                )
            )
        )
        assert rows == []


def _second_group_without_invite_bonus():
    return CampaignRules(
        participation_rules_version="v3.0",
        admission=InviteAdmission(
            type="invite",
            groups={
                str(FIRST): InviteAdmissionRule(required_valid_invites=2),
                str(SECOND): InviteAdmissionRule(required_valid_invites=2),
            },
        ),
        bonus_types=("invite",),
        invite=InviteRule(mode="ratio", valid_invites_per_ticket=2, retention_hours=0),
        group_invite_rules={str(FIRST): GroupInviteRule(invites_per_cycle=1)},
    )


async def test_closed_group_invite_bonus_does_not_use_campaign_ratio(session_factory):
    cid, pid = await seed_v3(session_factory, _second_group_without_invite_bonus())
    service = InvitationService(partial(UnitOfWork, session_factory))
    await confirm(service, await invite_link(session_factory, cid, pid, chat_id=SECOND), 1)
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        rows = list(await session.scalars(select(TelegramOutbox)))
        bonus = [
            row for row in rows if row.business_key.startswith("progress_incomplete:invite_bonus:")
        ]
        gates = [row for row in rows if row.business_key.startswith("progress_incomplete:invite:")]
        assert not participant.admission_qualified
        assert participant.ticket_total_cache == 1
        assert bonus == []
        assert gates == []


async def test_concurrent_messages_and_replays_keep_one_gate_and_correct_awards(session_factory):
    _cid, pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    service = ActivityService(partial(UnitOfWork, session_factory))
    messages = [
        MessageObservation(
            FIRST, number, 201, START + timedelta(seconds=number), f"并发发言有效消息序号{number}"
        )
        for number in range(1, 5)
    ]
    await asyncio.gather(*(service.record_message(message) for message in [*messages, *messages]))
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified and participant.ticket_total_cache == 3
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 4
        rows = list(await session.scalars(select(TelegramOutbox)))
        durable = [row for row in rows if not row.business_key.startswith("progress_incomplete:")]
        incomplete = [row for row in rows if row.business_key.startswith("progress_incomplete:")]
        assert len(durable) == 2
        assert incomplete == []
        assert all("delete_after_seconds" not in row.payload for row in durable)
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 3
