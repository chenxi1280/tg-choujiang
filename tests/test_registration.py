from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.participants import (
    ParticipantService,
    QualificationResult,
    RegistrationRequest,
)
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.enums import CampaignState, QualificationState, TicketSource
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    AuditLog,
    Campaign,
    Participant,
    QualificationCheck,
    ScheduledJob,
    TelegramOutbox,
    TicketLedger,
    UserProfile,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_registration_persists_pending_before_external_result(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(
            session,
            rules=CampaignRules(
                required_channel_id=-100999,
                bonus_types=("invite",),
            ),
        )
        campaign_id = campaign.id
    service = ParticipantService(
        partial(UnitOfWork, session_factory),
        invite_name_factory=lambda: "lottery_fixed_public",
    )
    attempt = await service.begin_registration(_request(campaign_id, now))
    async with session_factory() as session:
        participant = await session.get(Participant, attempt.participant_id)
        check = await session.get(QualificationCheck, attempt.qualification_check_id)
        outbox_count = await session.scalar(select(func.count(TelegramOutbox.id)))
        assert participant.qualification_state is QualificationState.PENDING
        assert check.state is QualificationState.PENDING
        assert check.observed_status == "checking"
        assert check.request_hash is not None
        assert outbox_count == 0
    await service.complete_registration(_result(attempt, now, QualificationState.ELIGIBLE))
    await service.complete_registration(_result(attempt, now, QualificationState.ELIGIBLE))
    async with session_factory() as session:
        participant = await session.get(Participant, attempt.participant_id)
        outbox_count = await session.scalar(select(func.count(TelegramOutbox.id)))
        invite_outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "create_invite_link")
        )
        registration_tickets = await session.scalar(
            select(func.count(TicketLedger.id)).where(
                TicketLedger.participant_id == attempt.participant_id,
                TicketLedger.source_type == TicketSource.REGISTRATION,
            )
        )
        assert participant.qualification_state is QualificationState.ELIGIBLE
        assert participant.ticket_total_cache == 1
        assert registration_tickets == 1
        assert outbox_count == 1
        assert invite_outbox.payload["name"] == "lottery_fixed_public"
        assert invite_outbox.payload["persistent"] is True
        assert participant.id.hex[:20] not in invite_outbox.payload["name"]


async def test_registration_does_not_create_invite_link_when_invite_bonus_is_off(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, rules=CampaignRules())
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
    attempt = await service.begin_registration(_request(campaign_id, now))

    await service.complete_registration(_result(attempt, now, QualificationState.ELIGIBLE))

    async with session_factory() as session:
        invite_outbox_count = await session.scalar(
            select(func.count(TelegramOutbox.id)).where(
                TelegramOutbox.operation == "create_invite_link"
            )
        )
    assert invite_outbox_count == 0


async def test_registration_after_cutoff_is_rejected(session_factory) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=1)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, cutoff_at=cutoff)
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
    with pytest.raises(UserFacingError, match="活动当前不接受报名"):
        await service.begin_registration(_request(campaign_id, datetime.now(UTC)))


async def test_registration_replay_reuses_source_update_check(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
    with bind_update_context(123, 900):
        first = await service.begin_registration(_request(campaign_id, now))
        replay = await service.begin_registration(_request(campaign_id, now))
        assert first.qualification_check_id == replay.qualification_check_id
        await service.complete_registration(_result(first, now, QualificationState.ELIGIBLE))
        completed = await service.begin_registration(_request(campaign_id, now))
    assert completed.requires_observation is False
    async with session_factory() as session:
        check_count = await session.scalar(
            select(func.count(QualificationCheck.id)).where(
                QualificationCheck.source_update_id == 900
            )
        )
        assert check_count == 1


async def test_late_registration_result_cannot_override_draw_qualification(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
    attempt = await service.begin_registration(_request(campaign_id, now))
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        participant = await session.get(Participant, attempt.participant_id)
        campaign.state = CampaignState.SETTLING
        participant.qualification_state = QualificationState.INELIGIBLE
        session.add(
            QualificationCheck(
                participant_id=participant.id,
                phase="draw",
                state=QualificationState.INELIGIBLE,
                observed_status="left",
                observed_at=now,
            )
        )
    completed = await service.complete_registration(
        _result(attempt, now, QualificationState.ELIGIBLE)
    )
    async with session_factory() as session:
        outbox_count = await session.scalar(select(func.count(TelegramOutbox.id)))
        registration = await session.get(QualificationCheck, attempt.qualification_check_id)
        assert completed.qualification_state is QualificationState.INELIGIBLE
        assert registration.state is QualificationState.ELIGIBLE
        assert outbox_count == 0


async def test_registration_enforces_username_region_and_all_required_chats(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(
        username_required=True,
        allowed_regions=("河南",),
        required_chat_ids=(-1002, -1003),
    )
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, rules=rules)
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
    no_username = RegistrationRequest(
        campaign_id=campaign_id,
        user_id=202,
        username=None,
        display_name="成员二",
        observed_at=now,
    )
    with pytest.raises(UserFacingError, match="Telegram 用户名"):
        await service.begin_registration(no_username)
    with pytest.raises(UserFacingError, match="请先使用 /region"):
        await service.begin_registration(_request(campaign_id, now))
    async with session_factory() as session, session.begin():
        session.add(UserProfile(user_id=202, region="河南", last_draft={}))

    attempt = await service.begin_registration(_request(campaign_id, now))

    assert attempt.required_chat_ids == (-1002, -1003)


async def test_full_draw_threshold_creates_one_system_trigger_in_registration_transaction(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(draw_mode="full", participant_target=2)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, rules=rules)
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
    attempt = await service.begin_registration(_request(campaign_id, now))

    await service.complete_registration(_result(attempt, now, QualificationState.ELIGIBLE))
    await service.complete_registration(_result(attempt, now, QualificationState.ELIGIBLE))

    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        jobs = (
            await session.scalars(
                select(ScheduledJob).where(ScheduledJob.aggregate_id == str(campaign_id))
            )
        ).all()
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "campaign.early_cutoff")
        )
        assert campaign.state is CampaignState.SETTLING
        assert campaign.cutoff_at == now
        assert {job.job_type for job in jobs} == {
            "qualification_review",
            "revoke_invite_links",
            "campaign_settle",
            "campaign_draw",
        }
        assert audit.actor_user_id is None


async def test_completion_message_deletion_job_is_durable_and_idempotent(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))

    for _ in range(2):
        await service.schedule_message_deletion(
            campaign_id=campaign_id,
            chat_id=202,
            message_id=77,
            run_at=now,
        )

    async with session_factory() as session:
        jobs = tuple(await session.scalars(select(ScheduledJob)))
        assert len(jobs) == 1
        assert jobs[0].job_type == "delete_telegram_message"
        assert jobs[0].payload == {"chat_id": 202, "message_id": 77}


def _request(campaign_id, observed_at: datetime) -> RegistrationRequest:
    return RegistrationRequest(
        campaign_id=campaign_id,
        user_id=202,
        username="member_two",
        display_name="成员二",
        observed_at=observed_at,
    )


def _result(attempt, observed_at: datetime, state: QualificationState) -> QualificationResult:
    return QualificationResult(
        participant_id=attempt.participant_id,
        qualification_check_id=attempt.qualification_check_id,
        state=state,
        observed_at=observed_at,
        observed_status="member",
    )
