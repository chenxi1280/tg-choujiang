from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

from sqlalchemy import select

from tests.test_v33_platform_runtime import MembershipBot, job, review, seed_platform
from tg_lottery.application.bot_relationships import BotRelationshipService
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import QualificationState, UpdateState
from tg_lottery.infrastructure.db import (
    BotRelationship,
    Campaign,
    Participant,
    QualificationCheck,
    TelegramUpdate,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


def retained_start(update_id, now, *, state=UpdateState.PROCESSED):
    return TelegramUpdate(
        bot_id=999,
        update_id=update_id,
        update_type="message",
        routing_chat_id=201,
        event_at=now,
        payload_hash="f" * 64,
        state=state,
        payload={
            "message": {
                "text": "/start",
                "chat": {"id": 201, "type": "private"},
                "from": {"id": 201, "is_bot": False},
            }
        },
    )


async def test_retained_processed_start_recovers_existing_user_without_restarting_bot(
    session_factory,
):
    now = datetime.now(UTC) - timedelta(days=1)
    async with session_factory() as session, session.begin():
        session.add(retained_start(42, now))
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    assert (await service.observation(201))["state"] == "eligible"
    async with session_factory() as session:
        relation = await session.get(BotRelationship, (999, 201))
        assert relation.start_update_id == 42 and relation.started_at == now


async def test_processed_history_cannot_hide_a_later_blocked_relationship(session_factory):
    now = datetime.now(UTC) - timedelta(days=1)
    async with session_factory() as session, session.begin():
        session.add(retained_start(100, now))
        session.add(
            TelegramUpdate(
                bot_id=999,
                update_id=1,
                update_type="my_chat_member",
                routing_chat_id=201,
                event_at=now + timedelta(seconds=1),
                payload_hash="e" * 64,
                state=UpdateState.PROCESSED,
                payload={
                    "my_chat_member": {
                        "chat": {"id": 201, "type": "private"},
                        "new_chat_member": {"user": {"id": 999}, "status": "kicked"},
                    }
                },
            )
        )
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    assert (await service.observation(201))["observed_status"] == "bot_blocked"


async def test_start_reply_failure_does_not_block_snapshot_after_start_fact_is_projected(
    session_factory,
):
    campaign_id, _ = await seed_platform(session_factory)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        session.add(retained_start(42, now, state=UpdateState.FAILED))
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    with bind_update_context(999, 42):
        await service.record(201, now, started=True)
    await review(session_factory).run(job(campaign_id))
    snapshot = await SettlementService(partial(UnitOfWork, session_factory)).settle(
        campaign_id, datetime.now(UTC)
    )
    assert snapshot.total_weight == 1


async def test_gate_failure_skips_unrelated_api_and_preserves_member_state(
    session_factory,
):
    from unittest.mock import AsyncMock

    campaign_id, participant_id = await seed_platform(session_factory)
    async with session_factory() as session, session.begin():
        participant = await session.get(Participant, participant_id)
        participant.admission_qualified = False
        participant.qualification_state = QualificationState.PENDING
    bot = MembershipBot(bot_status="member")
    bot.get_chat_member = AsyncMock(side_effect=RuntimeError("must not query excluded candidates"))
    await review(session_factory, bot=bot).run(job(campaign_id))
    bot.get_chat_member.assert_not_awaited()
    snapshot = await SettlementService(partial(UnitOfWork, session_factory)).settle(
        campaign_id, datetime.now(UTC)
    )
    assert snapshot.total_weight == 0
    async with session_factory() as session:
        assert (
            await session.get(Participant, participant_id)
        ).qualification_state is QualificationState.PENDING


async def test_username_and_region_are_rechecked_at_draw_review(session_factory):
    from tg_lottery.infrastructure.db import UserProfile

    campaign_id, participant_id = await seed_platform(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.rules = campaign.rules | {"username_required": True, "allowed_regions": ["四川"]}
        session.add(UserProfile(user_id=201, region="广东"))
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await relationships.record(201, datetime.now(UTC), started=True)
    bot = MembershipBot()
    original = bot.get_chat_member

    async def with_user(chat_id, user_id):
        result = await original(chat_id, user_id)
        result.user = SimpleNamespace(id=user_id, username=None)
        return result

    bot.get_chat_member = with_user
    await review(session_factory, bot=bot).run(job(campaign_id))
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        assert participant.qualification_state is QualificationState.INELIGIBLE
        check = await session.scalar(
            select(QualificationCheck)
            .where(QualificationCheck.phase == "draw")
            .order_by(QualificationCheck.observed_at.desc())
            .limit(1)
        )
        assert check.observations["profile"]["username_state"] == "ineligible"
        assert check.observations["profile"]["region_state"] == "ineligible"
