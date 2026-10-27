import hashlib

from sqlalchemy import func, select, text

from tests.test_base_activity_integration import START, messages, seed
from tg_lottery.application.activity import ActivityService
from tg_lottery.application.integration_snapshot import SnapshotRequest, campaign_snapshot
from tg_lottery.infrastructure.db import Campaign, Participant, TicketLedger
from tg_lottery.infrastructure.db.uow import UnitOfWork


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
