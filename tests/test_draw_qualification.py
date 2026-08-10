from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace

from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState, JobState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import QualificationCheck, ScheduledJob
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.jobs.handlers import JobHandlers


class QualificationBot:
    def __init__(self, statuses: dict[int, str]) -> None:
        self.statuses = statuses
        self.calls: list[int] = []

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.calls.append(chat_id)
        return SimpleNamespace(status=self.statuses[chat_id], is_member=False)


async def test_draw_review_rechecks_main_legacy_and_all_extra_chats(session_factory) -> None:
    rules = CampaignRules(required_channel_id=-1002, required_chat_ids=(-1003, -1004))
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(
            session,
            state=CampaignState.SETTLING,
            rules=rules,
        )
        campaign_id = campaign.id
        participant_id = participant.id
        main_chat_id = group.chat_id
    bot = QualificationBot(
        {main_chat_id: "member", -1002: "member", -1003: "left", -1004: "member"}
    )

    await _handlers(bot, session_factory).handle(_job(str(campaign_id)))

    async with session_factory() as session:
        check = await session.scalar(
            select(QualificationCheck).where(
                QualificationCheck.participant_id == participant_id,
                QualificationCheck.phase == "draw",
            )
        )
        assert check.state is QualificationState.INELIGIBLE
        assert [item["chat_id"] for item in check.observations["chats"]] == [
            main_chat_id,
            -1002,
            -1003,
            -1004,
        ]
    assert bot.calls == [main_chat_id, -1002, -1003, -1004]


def _handlers(bot: QualificationBot, session_factory) -> JobHandlers:
    uow_factory = partial(UnitOfWork, session_factory)
    return JobHandlers(
        bot=bot,
        session_factory=session_factory,
        participants=ParticipantService(uow_factory),
        invitations=InvitationService(uow_factory),
        settlement=SettlementService(uow_factory),
        drawing=DrawingService(uow_factory),
    )


def _job(campaign_id: str) -> ScheduledJob:
    return ScheduledJob(
        job_type="qualification_review",
        aggregate_id=campaign_id,
        business_key=f"test:qualification_review:{campaign_id}",
        payload={},
        run_at=datetime.now(UTC),
        state=JobState.CLAIMED,
        attempts=1,
    )
