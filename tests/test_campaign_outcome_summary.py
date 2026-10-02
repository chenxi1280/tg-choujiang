# ruff: noqa: RUF001

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import MultipleResultsFound

from tests.helpers import seed_campaign
from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts, load_campaign_outcome
from tg_lottery.application.queries import AdminCampaignSummary, QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import AuditLog, Campaign, DrawRun, DrawSnapshot
from tg_lottery.telegram.personal_flow import PersonalFlow

OWNER_ID = 101
OTHER_OWNER_ID = 102
COMPLETED_AT = datetime(2026, 10, 2, 12, tzinfo=UTC)
CANCELLED_AT = COMPLETED_AT - timedelta(hours=1)
FROZEN_WEIGHT = 1


@pytest.mark.parametrize(
    "state", [CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED]
)
async def test_completed_draw_has_priority_over_raw_state_and_cancellation(
    session_factory, *, state
):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=state)
        snapshot, run = await _add_draw(session, campaign, completed=True)
        session.add(_cancel_audit(campaign.id))
        await session.flush()
        saved_times = campaign.cutoff_at, campaign.draw_at

        facts = await load_campaign_outcome(session, campaign.id)

        assert facts == CampaignOutcomeFacts("drawn", snapshot.id, run.id, COMPLETED_AT)
        assert campaign.state is state
        assert (campaign.cutoff_at, campaign.draw_at) == saved_times
        assert snapshot.total_weight == FROZEN_WEIGHT


@pytest.mark.parametrize("state", [CampaignState.CANCELLED, CampaignState.ARCHIVED])
async def test_cancelled_outcome_requires_exact_campaign_cancel_fact(session_factory, *, state):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=state)
        await _add_draw(session, campaign, completed=False)
        session.add_all(
            [
                _cancel_audit(uuid4()),
                _cancel_audit(campaign.id, aggregate_type="participant"),
                _cancel_audit(campaign.id, action="campaign.archived"),
            ]
        )
        await session.flush()
        assert await load_campaign_outcome(session, campaign.id) == CampaignOutcomeFacts(
            "unconfirmed"
        )

        session.add_all(
            [_cancel_audit(campaign.id), _cancel_audit(campaign.id, created_at=COMPLETED_AT)]
        )
        await session.flush()
        assert await load_campaign_outcome(session, campaign.id) == CampaignOutcomeFacts(
            "cancelled", cancelled_at=CANCELLED_AT
        )


async def test_summary_and_created_lists_share_terminal_facts_without_duplicate_counts(
    session_factory,
):
    async with session_factory() as session, session.begin():
        expected = await _seed_summary_cases(session)
    queries = QueryService(session_factory)

    summary = await queries.admin_campaign_summary(OWNER_ID)

    assert summary == AdminCampaignSummary(
        total=sum(len(ids) for ids in expected.values()),
        active=len(expected["active"]),
        drawn=len(expected["drawn"]),
        cancelled=len(expected["cancelled"]),
        unconfirmed=len(expected["unconfirmed"]),
    )
    for category, expected_ids in expected.items():
        campaigns = await queries.created_campaigns(OWNER_ID, category)
        assert {campaign.id for campaign in campaigns} == expected_ids
        assert len(campaigns) == len(expected_ids)
    assert await queries.admin_campaign_summary(OTHER_OWNER_ID) == AdminCampaignSummary(
        total=1, active=0, drawn=1, cancelled=0
    )
    with pytest.raises(ValueError, match="抽奖分类无效"):
        await queries.created_campaigns(OWNER_ID, "unsupported")


async def test_multiple_completed_runs_are_exposed_and_never_multiply_summary(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=CampaignState.ARCHIVED)
        snapshot, _ = await _add_draw(session, campaign, completed=True)
        session.add(
            DrawRun(
                snapshot_id=snapshot.id,
                algorithm_version="another-version",
                random_input="saved-input",
                completed_at=COMPLETED_AT,
            )
        )
        campaign_id = campaign.id
    queries = QueryService(session_factory)

    with pytest.raises(MultipleResultsFound):
        await queries.campaign_outcome(campaign_id)
    assert await queries.admin_campaign_summary(OWNER_ID) == AdminCampaignSummary(1, 0, 1, 0)
    assert len(await queries.created_campaigns(OWNER_ID, "drawn")) == 1


@pytest.mark.parametrize("unconfirmed", [0, 1])
async def test_homepage_exposes_unconfirmed_terminal_count_only_when_present(unconfirmed):
    summary = AdminCampaignSummary(1, 0, 0, 0, unconfirmed)
    message = SimpleNamespace(from_user=SimpleNamespace(id=OWNER_ID), answer=AsyncMock())
    queries = SimpleNamespace(admin_campaign_summary=AsyncMock(return_value=summary))

    await PersonalFlow(profiles=None, queries=queries, bot_username="lottery_bot").show_index(
        message
    )

    text = message.answer.await_args.args[0]
    assert ("未确认（待核查）" in text) is bool(unconfirmed)
    if unconfirmed:
        assert "未确认（待核查）：1" in text


@pytest.mark.parametrize("category,label", [("drawn", "已开奖"), ("cancelled", "已取消")])
async def test_archived_created_buttons_keep_fact_classification(category, label):
    campaign = SimpleNamespace(
        name="历史活动", state=CampaignState.ARCHIVED, public_ref="archived-test-reference"
    )
    message = SimpleNamespace(from_user=SimpleNamespace(id=OWNER_ID), answer=AsyncMock())
    queries = SimpleNamespace(created_campaigns=AsyncMock(return_value=[campaign]))

    await PersonalFlow(profiles=None, queries=queries, bot_username="lottery_bot")._show_created(
        message, category
    )

    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].text == f"历史活动｜{label} · 已归档"


async def _seed_summary_cases(session):
    _, first, _ = await seed_campaign(session)
    cases = (
        (CampaignState.RUNNING, "active", "completed"),
        (CampaignState.PAUSED, "active", "none"),
        (CampaignState.ARCHIVED, "drawn", "both"),
        (CampaignState.CANCELLED, "drawn", "both"),
        (CampaignState.ARCHIVED, "cancelled", "cancelled"),
        (CampaignState.CANCELLED, "cancelled", "cancelled"),
        (CampaignState.ARCHIVED, "unconfirmed", "none"),
        (CampaignState.DRAWN, "unconfirmed", "unfinished"),
        (CampaignState.CANCELLED, "unconfirmed", "none"),
    )
    expected = {category: set() for category in ("active", "drawn", "cancelled", "unconfirmed")}
    for position, (state, category, fact) in enumerate(cases):
        campaign = first if position == 0 else _new_campaign(first, state=state)
        session.add(campaign)
        await session.flush()
        await _add_summary_fact(session, campaign, fact=fact)
        expected[category].add(campaign.id)
    other = _new_campaign(first, state=CampaignState.ARCHIVED, created_by=OTHER_OWNER_ID)
    session.add(other)
    await session.flush()
    await _add_draw(session, other, completed=True)
    session.add(_cancel_audit("not-a-uuid", aggregate_type="other"))
    return expected


async def _add_summary_fact(session, campaign, *, fact):
    if fact in {"completed", "both", "unfinished"}:
        await _add_draw(session, campaign, completed=fact != "unfinished")
    if fact in {"cancelled", "both"}:
        session.add_all([_cancel_audit(campaign.id), _cancel_audit(campaign.id)])
    await session.flush()


def _new_campaign(original, *, state, created_by=OWNER_ID):
    return Campaign(
        group_id=original.group_id,
        name="归档事实回归",
        state=state,
        timezone=original.timezone,
        preheat_at=original.preheat_at,
        cutoff_at=original.cutoff_at,
        draw_at=original.draw_at,
        rules=original.rules,
        config_revision=original.config_revision,
        created_by=created_by,
    )


async def _add_draw(session, campaign, *, completed):
    snapshot = DrawSnapshot(
        campaign_id=campaign.id,
        config_revision=campaign.config_revision,
        ledger_watermark=CANCELLED_AT,
        snapshot_hash=uuid4().hex + uuid4().hex,
        prize_plan_hash=campaign.prize_plan_hash,
        total_weight=FROZEN_WEIGHT,
    )
    session.add(snapshot)
    await session.flush()
    run = DrawRun(
        snapshot_id=snapshot.id,
        algorithm_version="outcome-test",
        random_input="saved-input",
        completed_at=COMPLETED_AT if completed else None,
    )
    session.add(run)
    await session.flush()
    return snapshot, run


def _cancel_audit(
    campaign_id,
    *,
    action="campaign.cancelled",
    aggregate_type="campaign",
    created_at=CANCELLED_AT,
):
    return AuditLog(
        actor_user_id=OWNER_ID,
        action=action,
        aggregate_type=aggregate_type,
        aggregate_id=str(campaign_id),
        created_at=created_at,
    )
