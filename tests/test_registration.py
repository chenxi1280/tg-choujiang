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
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Campaign, Participant, QualificationCheck, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_registration_persists_pending_before_external_result(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(
            session, rules=CampaignRules(required_channel_id=-100999)
        )
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
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
        assert participant.qualification_state is QualificationState.ELIGIBLE
        assert outbox_count == 1


async def test_registration_after_cutoff_is_rejected(session_factory) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=1)
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, cutoff_at=cutoff)
        campaign_id = campaign.id
    service = ParticipantService(partial(UnitOfWork, session_factory))
    with pytest.raises(ValueError, match="not accepting"):
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
