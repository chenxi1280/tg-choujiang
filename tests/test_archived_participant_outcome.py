# ruff: noqa: RUF001

from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tests.test_participant_draw_results import _completed_draw
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import AuditLog, Campaign, DrawSnapshot
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.member_record_text import participant_record_pages
from tg_lottery.telegram.participant_list_views import build_participated_campaign_list

ACTOR_ID = 101
PARTICIPANT_ID = 201


async def test_real_cancel_then_archive_keeps_cancelled_result_in_detail_and_list(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id, public_ref = campaign.id, campaign.public_ref
        original_times = campaign.cutoff_at, campaign.draw_at
    service = CampaignService(partial(UnitOfWork, session_factory))
    for state in (CampaignState.CANCELLED, CampaignState.ARCHIVED):
        await service.change_state(
            campaign_id, actor_user_id=ACTOR_ID, target=state, reason="归档取消历史验收"
        )

    queries = QueryService(session_factory)
    detail = await queries.participant_campaign_detail(PARTICIPANT_ID, public_ref)
    page = await queries.participated_campaign_page(PARTICIPANT_ID, category="ended", page=0)
    text = "\n".join(participant_record_pages(detail))
    listing, keyboard = build_participated_campaign_list(page, invitation_only=False)

    assert detail.state is CampaignState.ARCHIVED
    assert detail.outcome.kind == "cancelled"
    assert detail.draw_result is None
    assert "活动状态: 已取消（已归档）" in text
    assert "活动已取消，未开奖" in text + listing
    assert "未找到已完成" not in text and "未中奖" not in text
    assert "历史开奖结果未确认" not in text
    assert "活动已取消" in keyboard.inline_keyboard[1][0].text
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert (campaign.cutoff_at, campaign.draw_at) == original_times
        assert campaign.state is CampaignState.ARCHIVED
        assert await session.scalar(select(DrawSnapshot.id)) is None


@pytest.mark.parametrize("evidence", ["missing", "other_campaign", "other_action", "unfinished"])
@pytest.mark.parametrize("state", [CampaignState.ARCHIVED, CampaignState.CANCELLED])
async def test_archived_result_without_its_own_completed_or_cancel_fact_is_unconfirmed(
    session_factory, *, evidence, state
):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=state)
        public_ref = campaign.public_ref
        if evidence in {"other_campaign", "other_action"}:
            session.add(
                AuditLog(
                    actor_user_id=ACTOR_ID,
                    aggregate_type="campaign",
                    aggregate_id=str(uuid4() if evidence == "other_campaign" else campaign.id),
                    action="campaign.cancelled"
                    if evidence == "other_campaign"
                    else "campaign.archived",
                )
            )
        if evidence == "unfinished":
            await _unfinished_run(session, campaign)

    queries = QueryService(session_factory)
    detail = await queries.participant_campaign_detail(PARTICIPANT_ID, public_ref)
    page = await queries.participated_campaign_page(PARTICIPANT_ID, category="ended", page=0)
    text = "\n".join(participant_record_pages(detail))
    listing, _ = build_participated_campaign_list(page, invitation_only=False)

    assert detail.outcome.kind == "unconfirmed"
    assert detail.draw_result is None
    assert "活动状态: 历史结果未确认" in text
    assert "历史开奖结果未确认" in text
    assert "历史结果未确认，待核查" in listing
    assert "未中奖" not in text + listing
    assert "最终次数见详情" not in listing
    assert "活动已取消" not in text + listing


@pytest.mark.parametrize("state", [CampaignState.ARCHIVED, CampaignState.CANCELLED])
async def test_completed_draw_has_priority_over_cancellation_audit_after_archival(
    session_factory, *, state
):
    campaign_id, public_ref = await _completed_draw(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.state = state
        session.add(
            AuditLog(
                actor_user_id=ACTOR_ID,
                aggregate_type="campaign",
                aggregate_id=str(campaign_id),
                action="campaign.cancelled",
            )
        )
    queries = QueryService(session_factory)
    detail = await queries.participant_campaign_detail(PARTICIPANT_ID, public_ref)
    text = "\n".join(participant_record_pages(detail))
    page = await queries.participated_campaign_page(PARTICIPANT_ID, category="ended", page=0)
    listing, _ = build_participated_campaign_list(page, invitation_only=False)

    assert detail.outcome.kind == "drawn"
    assert detail.draw_result is not None
    assert detail.draw_result.frozen_tickets == 3
    assert len(detail.draw_result.awards) == 2
    assert "已中奖" in text and "最终开奖次数：3" in text
    assert "活动状态: 已开奖" in text
    assert "活动状态: 已取消" not in text
    assert "活动已取消" not in text
    assert "已开奖" in listing and "已取消" not in listing


async def _unfinished_run(session, campaign):
    from tg_lottery.infrastructure.db import DrawRun

    snapshot = DrawSnapshot(
        campaign_id=campaign.id,
        config_revision=campaign.config_revision,
        ledger_watermark=datetime.now(UTC),
        snapshot_hash="e" * 64,
        total_weight=0,
    )
    session.add(snapshot)
    await session.flush()
    session.add(DrawRun(snapshot_id=snapshot.id, algorithm_version="test", random_input="test"))
