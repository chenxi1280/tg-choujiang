from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.test_v33_platform_policy import policy
from tests.test_v33_unlimited_runtime import current_rules
from tests.v30_helpers import prepare_draw, seed_v3
from tg_lottery.application.bot_relationships import (
    BotRelationshipService,
)
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.platform_review import platform_snapshot_issue
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState, QualificationState, UpdateState
from tg_lottery.domain.errors import SettlementBlocked
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    BotRelationship,
    Campaign,
    CampaignOperationalBlock,
    CampaignRequirement,
    DrawSnapshot,
    Participant,
    PollingOffset,
    QualificationCheck,
    ScheduledJob,
    TelegramUpdate,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.jobs.errors import JobRetry
from tg_lottery.jobs.qualification_review import QualificationReviewService
from tg_lottery.telegram.platform_qualification import observe_campaign_memberships


class MembershipBot:
    id = 999

    def __init__(self, *, member_status="member", bot_status="administrator"):
        self.member_status = member_status
        self.bot_status = bot_status

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.bot_status if user_id == self.id else self.member_status)


def rules():
    return CampaignRules.model_validate(
        current_rules().model_dump(mode="json")
        | {
            "platform_policy": policy().model_dump(mode="json"),
            "required_chat_ids": list(policy().chat_ids),
        }
    )


async def seed_platform(factory):
    campaign_id, participant_id = await seed_v3(factory, rules())
    await prepare_draw(factory, campaign_id, participant_id)
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.state = CampaignState.SETTLING
        campaign.cutoff_at = now - timedelta(minutes=10)
        campaign.presentation = campaign.presentation | {
            "required_chats": [item.record() for item in policy().channels],
        }
        participant = await session.get(Participant, participant_id)
        participant.admission_qualified = True
        previous = await session.scalars(
            select(QualificationCheck).where(
                QualificationCheck.participant_id == participant_id,
                QualificationCheck.phase == "draw",
            )
        )
        for check in previous:
            check.observed_at = now - timedelta(hours=1)
        for index, channel in enumerate(policy().channels, 1):
            session.add(
                CampaignRequirement(
                    campaign_id=campaign_id,
                    chat_id_snapshot=channel.chat_id,
                    position=index,
                    chat_type="channel",
                    title=channel.title,
                    private=False,
                    public_join_url=channel.join_url,
                    resolved_at=now,
                )
            )
        session.add(
            PollingOffset(
                bot_id=999, last_durable_update_id=1, last_poll_succeeded_at=now, updated_at=now
            )
        )
    return campaign_id, participant_id


def review(factory, *, bot=None):
    return QualificationReviewService(
        bot=bot or MembershipBot(),
        session_factory=factory,
        participants=ParticipantService(partial(UnitOfWork, factory)),
        message_stats=None,
        requests_per_second=30,
    )


def job(campaign_id):
    return ScheduledJob(
        aggregate_id=str(campaign_id),
        job_type="qualification_review",
        attempts=1,
        business_key="unit-job",
        run_at=datetime.now(UTC),
    )


async def test_missing_start_cannot_pass_even_with_membership_in_all_channels(session_factory):
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    result = await observe_campaign_memberships(
        MembershipBot(),
        rules=rules(),
        chat_ids=policy().chat_ids,
        user_id=201,
        observed_at=datetime.now(UTC),
        relationship_loader=relationships.observation,
    )
    assert result.state is QualificationState.INELIGIBLE
    assert result.relationship["observed_status"] == "bot_not_started"
    assert all(row["state"] == "eligible" for row in result.observations)


async def test_bot_membership_without_admin_is_unknown_never_user_ineligibility(session_factory):
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await service.record(201, datetime.now(UTC), started=True)
    result = await observe_campaign_memberships(
        MembershipBot(bot_status="member"),
        rules=rules(),
        chat_ids=policy().chat_ids,
        user_id=201,
        observed_at=datetime.now(UTC),
        relationship_loader=service.observation,
    )
    assert result.state is QualificationState.UNKNOWN
    assert all(row["error_code"] == "bot_not_admin" for row in result.observations)


async def test_ordered_start_and_block_events_are_durable_and_not_replaced_by_old_start(
    session_factory,
):
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    now = datetime.now(UTC)
    with bind_update_context(999, 1):
        await service.record(201, now, started=True)
    with bind_update_context(999, 2):
        await service.record(201, now + timedelta(seconds=2), blocked=True)
    with bind_update_context(999, 1):
        await service.record(201, now, started=True)
    assert (await service.observation(201))["observed_status"] == "bot_blocked"
    with bind_update_context(999, 3):
        await service.record(201, now + timedelta(seconds=3), started=True)
    assert (await service.observation(201))["state"] == "eligible"
    with bind_update_context(999, 3):
        await service.record(201, now + timedelta(seconds=3), blocked=True)
    assert (await service.observation(201))["state"] == "unknown"
    async with session_factory() as session:
        row = await session.get(BotRelationship, (999, 201))
        assert row.started_at == now and row.start_update_id == 1


async def test_platform_review_never_degrades_after_permission_retry_exhaustion(session_factory):
    campaign_id, _ = await seed_platform(session_factory)
    service = review(session_factory, bot=MembershipBot(bot_status="member"))
    for _ in range(4):
        with pytest.raises(JobRetry, match="cannot degrade"):
            await service.run(job(campaign_id))
    async with session_factory() as session:
        blocks = tuple(await session.scalars(select(CampaignOperationalBlock)))
        assert blocks and all(item.resolved_at is None for item in blocks)
        assert any(item.retry_exhausted_at for item in blocks)
        checks = tuple(
            await session.scalars(
                select(QualificationCheck).where(
                    QualificationCheck.observed_status == "degraded_after_target_unavailable"
                )
            )
        )
        assert not checks


async def test_platform_membership_snapshot_is_current_and_late_relationship_events_block(
    session_factory,
):
    campaign_id, participant_id = await seed_platform(session_factory)
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await service.record(201, datetime.now(UTC), started=True)
    await review(session_factory).run(job(campaign_id))
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        assert await platform_snapshot_issue(session, campaign, now=datetime.now(UTC)) is None
        session.add(
            TelegramUpdate(
                bot_id=999,
                update_id=10,
                update_type="my_chat_member",
                routing_chat_id=201,
                event_at=datetime.now(UTC),
                payload_hash="a" * 64,
                payload={},
                state=UpdateState.RECEIVED,
            )
        )
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        assert (
            await platform_snapshot_issue(session, campaign, now=datetime.now(UTC))
            == "platform_relationship_inbox_changed"
        )
    with pytest.raises(JobRetry, match="Inbox"):
        await review(session_factory).run(job(campaign_id))
    await service.record(201, datetime.now(UTC) + timedelta(seconds=1), blocked=True)
    async with session_factory() as session:
        assert (
            await session.get(Participant, participant_id)
        ).qualification_state is QualificationState.INELIGIBLE


async def test_expired_qualification_schedules_refresh_and_never_freezes_candidates(
    session_factory,
):
    campaign_id, _ = await seed_platform(session_factory)
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await relationships.record(201, datetime.now(UTC), started=True)
    await review(session_factory).run(job(campaign_id))
    later = datetime.now(UTC) + timedelta(seconds=301)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        assert (
            await platform_snapshot_issue(session, campaign, now=later)
            == "platform_candidate_observation_expired"
        )
    with pytest.raises(SettlementBlocked, match="expired"):
        await SettlementService(partial(UnitOfWork, session_factory)).settle(campaign_id, later)
    async with session_factory() as session:
        assert await session.scalar(
            select(ScheduledJob.id).where(ScheduledJob.business_key.like("platform_review:%"))
        )
        assert await session.scalar(select(DrawSnapshot.id)) is None


async def test_frozen_snapshot_is_unchanged_by_later_block_events_and_review_jobs(session_factory):
    campaign_id, participant_id = await seed_platform(session_factory)
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await relationships.record(201, datetime.now(UTC), started=True)
    await review(session_factory).run(job(campaign_id))
    settlement = SettlementService(partial(UnitOfWork, session_factory))
    snapshot = await settlement.settle(campaign_id, datetime.now(UTC))
    assert snapshot.total_weight == 1
    await relationships.record(201, datetime.now(UTC) + timedelta(seconds=2), blocked=True)
    await review(session_factory).run(job(campaign_id))
    again = await settlement.settle(campaign_id, datetime.now(UTC) + timedelta(seconds=600))
    assert again.id == snapshot.id and again.snapshot_hash == snapshot.snapshot_hash
    async with session_factory() as session:
        assert (
            await session.get(Participant, participant_id)
        ).qualification_state is QualificationState.ELIGIBLE


async def test_same_second_conflict_does_not_assume_numeric_update_ids_are_event_order(
    session_factory,
):
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    now = datetime.now(UTC)
    with bind_update_context(999, 100):
        await service.record(201, now, started=True)
    with bind_update_context(999, 1):
        await service.record(201, now, blocked=True)
    assert (await service.observation(201))["state"] == "unknown"
    with bind_update_context(999, 2):
        await service.record(201, now + timedelta(seconds=1), started=True)
    assert (await service.observation(201))["state"] == "eligible"


async def test_start_fact_survives_a_failed_private_reply(session_factory):
    from unittest.mock import AsyncMock

    from aiogram.types import Chat, Message, User

    from tg_lottery.telegram.update_handler import LotteryUpdateHandler

    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    handler = LotteryUpdateHandler(
        bot=None,
        default_timezone="Asia/Shanghai",
        admin_flow=SimpleNamespace(suspend_draft=AsyncMock()),
        member_flow=SimpleNamespace(_relationships=service),
        personal_flow=SimpleNamespace(
            show_index=AsyncMock(side_effect=RuntimeError("UI render failure"))
        ),
        tokens=None,
        groups=None,
        activity=None,
        invitations=None,
        queries=None,
        stats=None,
        bot_username="lottery_bot",
        navigation=None,
    )
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        text="/start",
        chat=Chat(id=201, type="private"),
        from_user=User(id=201, is_bot=False, first_name="群友"),
    )
    with pytest.raises(RuntimeError, match="UI render failure"):
        await handler._private_message(message)
    assert (await service.observation(201))["observed_status"] == "bot_started"


async def test_private_bot_membership_event_never_deactivates_a_group():
    from unittest.mock import AsyncMock

    from tg_lottery.telegram.update_handler import LotteryUpdateHandler

    relationships = SimpleNamespace(record=AsyncMock())
    groups = SimpleNamespace(deactivate_group=AsyncMock())
    handler = LotteryUpdateHandler(
        bot=None,
        default_timezone="Asia/Shanghai",
        admin_flow=None,
        member_flow=SimpleNamespace(_relationships=relationships),
        personal_flow=None,
        tokens=None,
        groups=groups,
        activity=None,
        invitations=None,
        queries=None,
        stats=None,
        navigation=None,
    )
    event = SimpleNamespace(
        chat=SimpleNamespace(id=201, type="private"),
        date=datetime.now(UTC),
        new_chat_member=SimpleNamespace(status="kicked"),
    )
    await handler._my_chat_member(event)
    relationships.record.assert_awaited_once_with(201, event.date, blocked=True)
    groups.deactivate_group.assert_not_awaited()


async def test_unlimited_accumulated_rewards_and_snapshot_weight_exceed_32_bit(session_factory):
    from tg_lottery.domain.enums import TicketSource
    from tg_lottery.infrastructure.db import (
        CampaignParticipationGroup,
        DailyProgress,
        DrawEntry,
        TicketLedger,
    )

    campaign_id, participant_id = await seed_platform(session_factory)
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await relationships.record(201, datetime.now(UTC), started=True)
    await review(session_factory).run(job(campaign_id))
    async with session_factory() as session, session.begin():
        participant = await session.get(Participant, participant_id)
        participant.ticket_total_cache = 3_000_000_001
        target = await session.scalar(
            select(CampaignParticipationGroup)
            .where(CampaignParticipationGroup.campaign_id == campaign_id)
            .limit(1)
        )
        session.add(
            DailyProgress(
                campaign_id=campaign_id,
                participant_id=participant_id,
                participation_group_id=target.id,
                local_date=datetime.now(UTC).date(),
                valid_message_count=3_000_000_000,
                tickets_awarded=3_000_000_000,
            )
        )
        for index in range(2):
            session.add(
                TicketLedger(
                    campaign_id=campaign_id,
                    participant_id=participant_id,
                    source_type=TicketSource.POPULARITY,
                    delta=1_500_000_000,
                    source_ref=f"large-{index}",
                    business_key=f"large-{index}",
                    occurred_at=datetime.now(UTC),
                )
            )
    snapshot = await SettlementService(partial(UnitOfWork, session_factory)).settle(
        campaign_id, datetime.now(UTC)
    )
    assert snapshot.total_weight == 3_000_000_001
    async with session_factory() as session:
        entry = await session.scalar(select(DrawEntry).where(DrawEntry.snapshot_id == snapshot.id))
        assert entry.weight == 3_000_000_001
