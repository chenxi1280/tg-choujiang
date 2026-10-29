from datetime import UTC, datetime, timedelta

import pytest

from tests.helpers import _participant, seed_campaign
from tests.test_v32_navigation_database import _copy_campaign
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import CampaignOperationalBlock

OWNER = 101
MEMBER = 201
INSERTED_CAMPAIGNS = 12


async def test_creator_phase_categories_cover_every_state_once(session_factory):
    async with session_factory() as session, session.begin():
        _, first, _ = await seed_campaign(session)
        first.state = CampaignState.DRAFT
        expected = {CampaignState.DRAFT: first.id}
        for number, state in enumerate(tuple(CampaignState)[1:]):
            campaign = _copy_campaign(first, number)
            campaign.state = state
            session.add(campaign)
            await session.flush()
            expected[state] = campaign.id
    queries = QueryService(session_factory)
    phases = {
        "publishing": (CampaignState.DRAFT, CampaignState.PUBLISHING),
        "active": (CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED),
        "pending": (CampaignState.SETTLING, CampaignState.READY_TO_DRAW, CampaignState.DRAWING),
        "ended": (CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED),
    }
    seen = set()
    for category, states in phases.items():
        page = await queries.creator_campaign_page(OWNER, options={"category": category})
        assert {item.id for item in page.rows} == {expected[state] for state in states}
        seen.update(item.id for item in page.rows)
        if category == "ended":
            assert all(item.outcome_kind == "unconfirmed" for item in page.rows)
    assert seen == set(expected.values())


@pytest.mark.parametrize("invitation_only", [False, True])
async def test_return_anchor_relocates_after_insertion_and_is_scoped_to_person(
    session_factory, invitation_only
):
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, first, participant = await seed_campaign(session)
        first.rules = first.rules | {"bonus_types": ["invite"]}
        first.created_at = participant.created_at = now - timedelta(days=1)
        participant.counting_started_at = first.preheat_at
        anchor = first.public_ref
        for number in range(INSERTED_CAMPAIGNS):
            campaign = _copy_campaign(first, number)
            campaign.created_at = now
            session.add(campaign)
            await session.flush()
            added = _participant(campaign)
            added.created_at = now
            added.counting_started_at = campaign.preheat_at
            session.add(added)
    queries = QueryService(session_factory)
    creator = await queries.creator_campaign_page(
        OWNER, options={"category": "active", "page": 0, "anchor": anchor}
    )
    assert creator.page == 2 and any(item.public_ref == anchor for item in creator.rows)
    member = await queries.participated_campaign_page(
        MEMBER, category="active", page=0, anchor=anchor, invitation_only=invitation_only
    )
    assert member.page == 1 and any(item.public_ref == anchor for item in member.rows)
    foreign = await queries.participated_campaign_page(
        MEMBER + 1, category="active", page=1, anchor=anchor
    )
    assert foreign.rows == () and foreign.anchor_missing
    missing = await queries.creator_campaign_page(
        OWNER, options={"category": "pending", "page": 9, "anchor": anchor}
    )
    assert missing.page == 0 and missing.anchor_missing
    first_page = await queries.participated_campaign_page(MEMBER, category="active", page=0)
    second_page = await queries.participated_campaign_page(MEMBER, category="active", page=1)
    assert not {item.public_ref for item in first_page.rows} & {
        item.public_ref for item in second_page.rows
    }


async def test_detail_and_management_read_all_unresolved_block_sources(session_factory):
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        reference, campaign_id, cutoff = campaign.public_ref, campaign.id, campaign.cutoff_at
        for number, source in enumerate(("qualification_review", "telegram_outbox")):
            session.add(
                CampaignOperationalBlock(
                    campaign_id=campaign_id,
                    target_kind="participation_group",
                    chat_id=-100123 - number,
                    error_code="bot_not_member",
                    source=source,
                    details="bot is not a member",
                    first_detected_at=now,
                    last_observed_at=now,
                )
            )
        session.add(
            CampaignOperationalBlock(
                campaign_id=campaign_id,
                target_kind="requirement",
                chat_id=-100125,
                error_code="resolved",
                source="telegram_outbox",
                first_detected_at=now,
                last_observed_at=now,
                resolved_at=now,
            )
        )
    queries = QueryService(session_factory)
    detail = await queries.participant_campaign_detail(MEMBER, reference)
    assert detail.cutoff_at == cutoff
    assert {issue.source for issue in detail.operational_issues} == {
        "qualification_review",
        "telegram_outbox",
    }
    assert (
        await queries.management_details(campaign_id)
    ).operational_issues == detail.operational_issues
    assert len(await queries.operational_issues(campaign_id)) == 2
