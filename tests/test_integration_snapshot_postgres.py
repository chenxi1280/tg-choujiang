import hashlib

from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select, text

from tests.test_base_activity_integration import START, messages, seed
from tg_lottery.application.activity import ActivityService
from tg_lottery.application.integration_snapshot import SnapshotRequest, campaign_snapshot
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Participant,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.integration_api import create_app
from tg_lottery.integration_config import IntegrationSettings


async def test_snapshot_reads_accepted_facts_and_does_not_create_participants(session_factory):
    campaign_id, _participant_id = await seed(session_factory, days=1, messages=2)
    await messages(ActivityService(lambda: UnitOfWork(session_factory)), day=0, count=2)
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        reference, creator = campaign.public_ref, campaign.created_by
        before = await session.scalar(select(func.count(TicketLedger.id)))
    request = SnapshotRequest(
        user_ids=[201, 9999999999], date_from=START.date(), date_to=START.date()
    )
    async with session_factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        snapshot = await campaign_snapshot(
            session, reference, request, creator_ids={creator}, bot_id=123
        )
        digest_request = SnapshotRequest(
            user_digests=[
                hashlib.sha256(b"201").hexdigest(),
                hashlib.sha256(b"9999999999").hexdigest(),
            ],
            date_from=START.date(),
            date_to=START.date(),
        )
        digest_snapshot = await campaign_snapshot(
            session, reference, digest_request, creator_ids={creator}, bot_id=123
        )
        assert digest_snapshot["participants"][0]["user_id"] == "201"
        assert digest_snapshot["participants"][1]["registered"] is False
        assert snapshot["participants"][0]["notification_delivery"]
        assert all(
            row["chat_id"] == "-100123"
            for row in snapshot["participants"][0]["notification_delivery"]
        )
        assert snapshot["participants"][0]["behavior_qualified"]
        assert snapshot["participants"][0]["base_counts"][0]["accepted_messages"] == 2
        assert snapshot["participants"][1] == {"user_id": "9999999999", "registered": False}
        assert (
            await campaign_snapshot(
                session, reference, request, creator_ids={creator + 1}, bot_id=123
            )
            is None
        )
    async with session_factory() as session:
        assert await session.scalar(select(func.count(TicketLedger.id))) == before
        assert await session.scalar(select(func.count(Participant.id))) == 1


async def test_snapshot_http_returns_registration_tickets_as_json_integers(session_factory):
    campaign_id, _ = await seed(session_factory, days=1, messages=2)
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        reference, creator = campaign.public_ref, campaign.created_by
    settings = IntegrationSettings(token="x" * 32, creator_ids=[creator])
    app = create_app(session_factory, settings, bot_id=123)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            f"/v1/campaigns/{reference}/snapshot",
            headers={"Authorization": "Bearer " + "x" * 32},
            json={
                "user_digests": [hashlib.sha256(b"201").hexdigest()],
                "date_from": START.date().isoformat(),
                "date_to": START.date().isoformat(),
            },
        )
        assert response.status == 200
        payload = await response.json()
        participant = payload["participants"][0]
        assert participant["registered"] is True
        assert participant["ticket_total"] == 1
        assert participant["ticket_ledger_by_source"] == {"registration": 1}
        assert type(participant["ticket_ledger_by_source"]["registration"]) is int


async def test_snapshot_exposes_only_confirmed_public_group_identities(session_factory):
    campaign_id, _ = await seed(session_factory, days=1, messages=2)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        reference, creator = campaign.public_ref, campaign.created_by
        groups = list(
            await session.scalars(
                select(CampaignParticipationGroup)
                .where(CampaignParticipationGroup.campaign_id == campaign_id)
                .order_by(CampaignParticipationGroup.position)
            )
        )
        groups[0].username_snapshot = "public_group"
        groups[0].private = False
        groups[0].publication_state = "confirmed"
        groups[1].username_snapshot = "private_group"
        groups[1].private = True
        groups[1].publication_state = "confirmed"
    request = SnapshotRequest(user_ids=[201], date_from=START.date(), date_to=START.date())
    async with session_factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        snapshot = await campaign_snapshot(
            session, reference, request, creator_ids={creator}, bot_id=123
        )
    assert snapshot["group_identities"] == [{"chat_id": "-100123", "username": "public_group"}]


async def test_explicit_campaign_scope_does_not_authorize_other_campaigns_by_same_creator(
    session_factory,
):
    campaign_id, _ = await seed(session_factory, days=1, messages=2)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        other = Campaign(
            group_id=campaign.group_id,
            name="other",
            state=campaign.state,
            timezone=campaign.timezone,
            preheat_at=campaign.preheat_at,
            cutoff_at=campaign.cutoff_at,
            draw_at=campaign.draw_at,
            rules=campaign.rules,
            created_by=campaign.created_by,
        )
        session.add(other)
        await session.flush()
        reference, other_reference, creator = (
            campaign.public_ref,
            other.public_ref,
            campaign.created_by,
        )
    request = SnapshotRequest(user_ids=[201], date_from=START.date(), date_to=START.date())
    async with session_factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        allowed = await campaign_snapshot(
            session,
            reference,
            request,
            creator_ids={creator + 1},
            bot_id=123,
            campaign_refs={reference},
        )
        other = await campaign_snapshot(
            session,
            other_reference,
            request,
            creator_ids={creator + 1},
            bot_id=123,
            campaign_refs={reference},
        )
    assert allowed["campaign_ref"] == reference
    assert other is None
    await _assert_http_campaign_scope(session_factory, reference, other_reference, creator=creator)


async def _assert_http_campaign_scope(session_factory, reference, other_reference, *, creator):
    settings = IntegrationSettings(
        token="x" * 32, creator_ids=[creator + 1], campaign_refs=[reference]
    )
    app = create_app(session_factory, settings, bot_id=123)
    body = {
        "user_ids": [201],
        "date_from": START.date().isoformat(),
        "date_to": START.date().isoformat(),
    }
    async with TestClient(TestServer(app)) as client:
        headers = {"Authorization": "Bearer " + "x" * 32}
        assert (
            await client.post(f"/v1/campaigns/{reference}/snapshot", headers=headers, json=body)
        ).status == 200
        assert (
            await client.post(
                f"/v1/campaigns/{other_reference}/snapshot", headers=headers, json=body
            )
        ).status == 404
