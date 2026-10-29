from dataclasses import replace
from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import delete, func, select

from tests.v30_helpers import FIRST, SECOND, START, rules_for, seed_v3, speech
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.admission_views import admission_progress_text
from tg_lottery.application.records import add_outbox
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import (
    AdmissionProgress,
    AuditLog,
    BaseActivityEvent,
    Campaign,
    CampaignParticipationGroup,
    Participant,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.ops.reset_message_window import WindowOptions, reset_message_window

NEW_START = START + timedelta(days=2, hours=1)


async def _seed(factory, monkeypatch):
    rules = rules_for(
        "message", ("activity",), groups={str(FIRST): dict(daily_messages=2, consecutive_days=3)}
    )
    cid, pid = await seed_v3(factory, rules)
    async with factory() as session, session.begin():
        await session.execute(
            delete(CampaignParticipationGroup).where(
                CampaignParticipationGroup.campaign_id == cid,
                CampaignParticipationGroup.chat_id_snapshot == SECOND,
            )
        )
        campaign = await session.get(Campaign, cid)
        options = WindowOptions(
            campaign_id=cid,
            expected_name=campaign.name,
            expected_chat_id=FIRST,
            starts_at=NEW_START,
            expected_cutoff=campaign.cutoff_at,
            expected_draw=campaign.draw_at,
            repair_ref="test-authorized-window-correction",
        )
    monkeypatch.setattr(
        "tg_lottery.ops.reset_message_window.utcnow", lambda: NEW_START + timedelta(minutes=5)
    )
    for day in range(3):
        await speech(factory, count=2, at=START + timedelta(days=day), first_id=1 + day * 10)
    await speech(factory, count=1, at=NEW_START, first_id=100)
    return cid, pid, options


async def test_preview_apply_replay_future_awards_and_final_gate(session_factory, monkeypatch):
    cid, pid, options = await _seed(session_factory, monkeypatch)
    report = await reset_message_window(session_factory, options)
    assert report["revoked_group_completions"] == 1 and report["ticket_delta"] == -3
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
    applied = replace(options, expected_fingerprint=report["fingerprint"])
    await reset_message_window(session_factory, applied)
    assert (await reset_message_window(session_factory, applied))["status"] == "already_applied"
    await _verify_corrected_state(session_factory, cid, pid)
    await speech(session_factory, count=1, at=NEW_START + timedelta(minutes=1), first_id=101)
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.ticket_total_cache == 2 and not participant.admission_qualified
        text = await admission_progress_text(
            session,
            await session.get(Campaign, cid),
            participant,
            now=NEW_START + timedelta(minutes=3),
        )
        assert "今日任务已完成" in text and "已完成 1/3 天" in text
        assert "待开始" in text and "当前有效抽奖次数: 0" in text
    for day in (1, 2):
        await speech(
            session_factory,
            count=2,
            at=NEW_START + timedelta(days=day) - timedelta(hours=2),
            first_id=200 + day * 10,
        )
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified and participant.ticket_total_cache == 4
        entries = await SettlementService(None)._eligible_entries(session, cid)
        assert [(p.id, weight) for p, weight in entries] == [(pid, 4)]
        assert await session.scalar(select(func.count(AdmissionProgress.id))) == 1


async def _verify_corrected_state(factory, cid, pid):
    async with factory() as session:
        participant = await session.get(Participant, pid)
        assert not participant.admission_qualified and participant.ticket_total_cache == 1
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 1
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 7
        assert (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "campaign.message_window_reset"
                )
            )
            == 1
        )
        assert (await session.scalar(select(AdmissionProgress))).qualified is False
        assert await SettlementService(None)._eligible_entries(session, cid) == []


async def test_stale_identity_and_claimed_delivery_cannot_mutate(session_factory, monkeypatch):
    _cid, pid, options = await _seed(session_factory, monkeypatch)
    report = await reset_message_window(session_factory, options)
    with pytest.raises(ValueError, match="configuration changed"):
        await reset_message_window(session_factory, replace(options, expected_fingerprint="stale"))
    async with session_factory() as session, session.begin():
        outbox = add_outbox(
            session,
            operation="send_progress_notice",
            aggregate_type="participant",
            aggregate_id=str(pid),
            recipient_chat_id=FIRST,
            business_key="claimed-progress",
            payload={},
        )
        outbox.state = OutboxState.CLAIMED
    with pytest.raises(ValueError, match="in flight"):
        await reset_message_window(
            session_factory, replace(options, expected_fingerprint=report["fingerprint"])
        )
    async with session_factory() as session:
        assert (await session.get(Participant, pid)).admission_qualified
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 4


async def test_repair_restarts_dedup_at_boundary_and_keeps_source_rows(
    session_factory, monkeypatch
):
    _cid, pid, options = await _seed(session_factory, monkeypatch)
    service = ActivityService(partial(UnitOfWork, session_factory))
    for mid, offset in ((300, -1), (301, 10)):
        await service.record_message(
            MessageObservation(
                FIRST, mid, 201, NEW_START + timedelta(seconds=offset), "相同内容跨任务开始边界"
            )
        )
    report = await reset_message_window(session_factory, options)
    assert len(report["event_corrections"]["base_activity_events"]) == 1
    await reset_message_window(
        session_factory, replace(options, expected_fingerprint=report["fingerprint"])
    )
    async with session_factory() as session:
        corrected = await session.scalar(
            select(BaseActivityEvent).where(BaseActivityEvent.message_id == 301)
        )
        assert corrected.accepted
        assert (await session.get(Participant, pid)).ticket_total_cache == 2
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 9
