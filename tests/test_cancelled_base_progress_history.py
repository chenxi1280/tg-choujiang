from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.helpers import seed_campaign
from tg_lottery.application import base_activity_views
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import BaseActivityRule, CampaignRules
from tg_lottery.infrastructure.db import AuditLog, BaseActivityEvent, DrawRun, DrawSnapshot

CANCELLED_AT = datetime(2026, 10, 2, 12, tzinfo=UTC)
CHAT_ID = -100123
DAILY_MESSAGES = 5
REQUIRED_DAYS = 7
SNAPSHOT_HASH = "c" * 64
FROZEN_WEIGHT = 2
RECORDED_DAYS = 2
FUTURE_CUTOFF_DAYS = 8
VIEW_DAY_OFFSETS = (0, 1, 2, 20)


@pytest.mark.parametrize("state", [CampaignState.CANCELLED, CampaignState.ARCHIVED])
async def test_cancelled_progress_stays_at_recorded_day_after_updates_and_archival(
    session_factory, monkeypatch, *, state
):
    async with session_factory() as session, session.begin():
        campaign, participant, snapshot = await _seed_history(session, state=state)
        before = (
            campaign.cutoff_at,
            campaign.draw_at,
            snapshot.snapshot_hash,
            snapshot.total_weight,
        )
        outputs = []
        for days_later in VIEW_DAY_OFFSETS:
            viewed_at = CANCELLED_AT + timedelta(days=days_later)
            campaign.updated_at = viewed_at
            await session.flush()
            monkeypatch.setattr(base_activity_views, "utcnow", lambda value=viewed_at: value)
            outputs.append(
                await base_activity_views.base_progress_text(session, campaign, participant)
            )

        assert len(set(outputs)) == 1
        assert "记录日期 2026-10-02 5/5 条; 连续 2/7 天" in outputs[0]
        assert "本期基础发言已停止累计" in outputs[0]
        assert "当前有效抽奖次数" not in outputs[0]
        assert (
            campaign.cutoff_at,
            campaign.draw_at,
            snapshot.snapshot_hash,
            snapshot.total_weight,
        ) == (before)


async def test_cancellation_after_cutoff_keeps_original_cutoff_day(session_factory, monkeypatch):
    cutoff_at = CANCELLED_AT - timedelta(days=1)
    monkeypatch.setattr(base_activity_views, "utcnow", lambda: CANCELLED_AT + timedelta(days=20))
    async with session_factory() as session, session.begin():
        campaign, participant, _ = await _seed_history(session, cutoff_at=cutoff_at)

        text = await base_activity_views.base_progress_text(session, campaign, participant)

        assert "记录日期 2026-10-01 5/5 条; 连续 2/7 天" in text
        assert campaign.cutoff_at == cutoff_at


@pytest.mark.parametrize("state", [CampaignState.DRAWN, CampaignState.ARCHIVED])
async def test_drawn_archival_does_not_treat_other_audits_as_cancellation(
    session_factory, monkeypatch, *, state
):
    cutoff_at = CANCELLED_AT - timedelta(days=1)
    monkeypatch.setattr(base_activity_views, "utcnow", lambda: CANCELLED_AT + timedelta(days=20))
    async with session_factory() as session, session.begin():
        campaign, participant, snapshot = await _seed_history(
            session, state=state, cutoff_at=cutoff_at, with_audit=False
        )
        session.add_all(
            [
                _audit(campaign.id, action="campaign.archived"),
                DrawRun(
                    snapshot_id=snapshot.id,
                    algorithm_version="history-test",
                    random_input="saved-random-input",
                    completed_at=CANCELLED_AT,
                ),
            ]
        )
        await session.flush()

        text = await base_activity_views.base_progress_text(session, campaign, participant)

        assert "记录日期 2026-10-01 5/5 条; 连续 2/7 天" in text
        assert "未确认" not in text


@pytest.mark.parametrize(
    "state", [CampaignState.CANCELLED, CampaignState.ARCHIVED, CampaignState.DRAWN]
)
@pytest.mark.parametrize("with_unfinished_run", [False, True])
async def test_terminal_progress_requires_its_own_completion_or_cancellation_fact(
    session_factory, monkeypatch, *, state, with_unfinished_run
):
    async with session_factory() as session, session.begin():
        campaign, participant, snapshot = await _seed_history(
            session, state=state, with_audit=False
        )
        public_ref = campaign.public_ref
        user_id = participant.user_id
        session.add_all(
            [
                _audit(campaign.id, action="campaign.paused"),
                _audit(uuid4(), action="campaign.cancelled"),
            ]
        )
        if with_unfinished_run:
            session.add(
                DrawRun(
                    snapshot_id=snapshot.id,
                    algorithm_version="history-test",
                    random_input="saved-random-input",
                )
            )
        await session.flush()

    queries = QueryService(session_factory)
    outputs = []
    for days_later in VIEW_DAY_OFFSETS:
        viewed_at = CANCELLED_AT + timedelta(days=days_later)
        monkeypatch.setattr(base_activity_views, "utcnow", lambda value=viewed_at: value)
        detail = await queries.participant_campaign_detail(user_id, public_ref)
        outputs.append(detail.base_progress)

    assert len(set(outputs)) == 1
    assert detail is not None
    assert detail.state is state
    assert "无法确认记录截止日期" in detail.base_progress
    assert "缺少取消时间审计记录" in detail.base_progress
    assert "历史条数和连续进度未确认" in detail.base_progress
    assert "记录日期 2026-10" not in detail.base_progress
    assert "连续 2/7 天" not in detail.base_progress


async def _seed_history(
    session,
    *,
    state=CampaignState.CANCELLED,
    cutoff_at=None,
    with_audit=True,
):
    rule = BaseActivityRule(consecutive_days=REQUIRED_DAYS, daily_messages=DAILY_MESSAGES)
    _, campaign, participant = await seed_campaign(
        session,
        state=state,
        cutoff_at=cutoff_at or CANCELLED_AT + timedelta(days=FUTURE_CUTOFF_DAYS),
        rules=CampaignRules(base_activity_rules={str(CHAT_ID): rule}),
    )
    participant.base_qualified = False
    campaign.presentation = {"participation_groups": [{"id": CHAT_ID, "title": "验收群"}]}
    last_day = min(CANCELLED_AT, campaign.cutoff_at).replace(hour=2)
    session.add_all(_events(campaign.id, participant.id, last_day=last_day))
    if with_audit:
        session.add(_audit(campaign.id, action="campaign.cancelled"))
    snapshot = DrawSnapshot(
        campaign_id=campaign.id,
        config_revision=campaign.config_revision,
        ledger_watermark=last_day,
        snapshot_hash=SNAPSHOT_HASH,
        prize_plan_hash=campaign.prize_plan_hash,
        total_weight=FROZEN_WEIGHT,
    )
    session.add(snapshot)
    await session.flush()
    return campaign, participant, snapshot


def _events(campaign_id, participant_id, *, last_day):
    return [
        BaseActivityEvent(
            campaign_id=campaign_id,
            participant_id=participant_id,
            chat_id=CHAT_ID,
            message_id=day * DAILY_MESSAGES + number,
            event_at=last_day - timedelta(days=day, minutes=number),
            local_date=(last_day - timedelta(days=day)).date(),
            content_hash=f"{day * DAILY_MESSAGES + number:064x}",
            accepted=True,
        )
        for day in range(RECORDED_DAYS)
        for number in range(DAILY_MESSAGES)
    ]


def _audit(campaign_id, *, action):
    return AuditLog(
        actor_user_id=101,
        action=action,
        aggregate_type="campaign",
        aggregate_id=str(campaign_id),
        created_at=CANCELLED_AT,
    )
