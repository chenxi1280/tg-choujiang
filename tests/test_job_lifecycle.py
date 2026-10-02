from datetime import UTC, datetime

from sqlalchemy import select

from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState, JobState
from tg_lottery.infrastructure.db import InviteLink, ScheduledJob, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.jobs.handlers import JobHandlers


async def test_cancelled_campaign_absorbs_remaining_lifecycle_jobs(session_factory) -> None:
    from functools import partial

    from tests.helpers import seed_campaign

    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=CampaignState.CANCELLED)
        campaign_id = campaign.id
    uow_factory = partial(UnitOfWork, session_factory)
    handlers = JobHandlers(
        bot=object(),
        session_factory=session_factory,
        participants=ParticipantService(uow_factory),
        invitations=InvitationService(uow_factory),
        settlement=SettlementService(uow_factory),
        drawing=DrawingService(uow_factory),
    )
    for job_type in (
        "campaign_start",
        "qualification_review",
        "campaign_settle",
        "campaign_draw",
    ):
        await handlers.handle(_job(job_type, str(campaign_id)))


async def test_message_deletion_job_creates_one_outbox_intent(session_factory) -> None:
    from tests.helpers import seed_campaign

    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
    handlers = _handlers(session_factory)
    job = _job("delete_telegram_message", str(campaign_id))
    job.payload = {"chat_id": 202, "message_id": 77}

    await handlers.handle(job)
    await handlers.handle(job)

    async with session_factory() as session:
        rows = tuple(await session.scalars(select(TelegramOutbox)))
        assert len(rows) == 1
        assert rows[0].operation == "delete_message"
        assert rows[0].payload == {"message_id": 77}


async def test_cutoff_updates_published_campaign_card(session_factory) -> None:
    from tests.helpers import seed_campaign

    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=CampaignState.RUNNING)
        campaign.published_message_id = 321
        campaign_id = campaign.id

    await _handlers(session_factory).handle(_job("campaign_cutoff", str(campaign_id)))

    async with session_factory() as session:
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "edit_campaign_card_state")
        )
        assert row.payload["card_state"] == "settling"
        assert row.payload["message_id"] == 321


async def test_only_campaign_cancellation_revokes_personal_invite_links(session_factory) -> None:
    from tests.helpers import seed_campaign

    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        campaign_id = campaign.id
        session.add(
            InviteLink(
                campaign_id=campaign.id,
                participant_id=participant.id,
                link_hash="a" * 64,
                encrypted_url=b"ciphertext",
                telegram_name="lot_personal",
                expire_at=campaign.cutoff_at,
            )
        )
    handlers = _handlers(session_factory)

    await handlers.handle(_job("revoke_invite_links", str(campaign_id)))
    async with session_factory() as session:
        count = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "revoke_invite_link")
        )
        assert count is None

    cancellation = _job("revoke_invite_links", str(campaign_id))
    cancellation.payload = {"revoke_personal": True}
    await handlers.handle(cancellation)
    async with session_factory() as session:
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "revoke_invite_link")
        )
        assert row is not None


def _handlers(session_factory) -> JobHandlers:
    from functools import partial

    uow_factory = partial(UnitOfWork, session_factory)
    return JobHandlers(
        bot=object(),
        session_factory=session_factory,
        participants=ParticipantService(uow_factory),
        invitations=InvitationService(uow_factory),
        settlement=SettlementService(uow_factory),
        drawing=DrawingService(uow_factory),
    )


def _job(job_type: str, aggregate_id: str) -> ScheduledJob:
    return ScheduledJob(
        job_type=job_type,
        aggregate_id=aggregate_id,
        business_key=f"test:{job_type}",
        payload={},
        run_at=datetime.now(UTC),
        state=JobState.CLAIMED,
        attempts=1,
    )


async def test_campaign_draw_converts_user_facing_error_to_job_retry() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest

    from tg_lottery.domain.errors import UserFacingError
    from tg_lottery.jobs.errors import JobRetry

    drawing = SimpleNamespace(
        run=AsyncMock(
            side_effect=UserFacingError("指定中奖人未通过最终资格复核, 请重新指定或删除该名额")
        )
    )
    handlers = JobHandlers(
        bot=object(),
        session_factory=object(),
        participants=object(),
        invitations=object(),
        settlement=object(),
        drawing=drawing,
    )
    handlers._campaign_state = AsyncMock(return_value=CampaignState.READY_TO_DRAW)

    with pytest.raises(JobRetry) as exc_info:
        await handlers.handle(_job("campaign_draw", "00000000-0000-0000-0000-000000000001"))
    assert "指定中奖人未通过最终资格复核" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, UserFacingError)


async def test_campaign_draw_preserves_data_integrity_error() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest

    drawing = SimpleNamespace(
        run=AsyncMock(side_effect=ValueError("campaign prize plan changed after publication"))
    )
    handlers = JobHandlers(
        bot=object(),
        session_factory=object(),
        participants=object(),
        invitations=object(),
        settlement=object(),
        drawing=drawing,
    )
    handlers._campaign_state = AsyncMock(return_value=CampaignState.READY_TO_DRAW)
    with pytest.raises(ValueError, match="prize plan changed"):
        await handlers.handle(_job("campaign_draw", "00000000-0000-0000-0000-000000000001"))
