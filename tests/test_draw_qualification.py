from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256
from types import SimpleNamespace

from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState, InvitationState, JobState, QualificationState
from tg_lottery.domain.rules import CampaignRules, InviteRewardTier, InviteRule
from tg_lottery.infrastructure.db import (
    Invitation,
    InviteLink,
    QualificationCheck,
    ScheduledJob,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.jobs.handlers import JobHandlers


class QualificationBot:
    def __init__(self, statuses: dict[int | tuple[int, int], str]) -> None:
        self.statuses = statuses
        self.calls: list[int] = []

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.calls.append(chat_id)
        status = self.statuses.get((chat_id, user_id), self.statuses.get(chat_id))
        return SimpleNamespace(status=status, is_member=False)


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


async def test_draw_review_does_not_backdate_a_post_cutoff_membership_snapshot(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    rules = _single_invite_bonus_rule()
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(
            session, rules=rules, cutoff_at=now + timedelta(hours=1)
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"draw-review-invite").hexdigest(),
            telegram_name="lot_draw_review",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        values = campaign.id, group.chat_id, participant.user_id, link.id
    campaign_id, chat_id, participant_user_id, link_id = values
    invitations = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await invitations.record_joined(
        JoinObservation(link_id, 777, now - timedelta(hours=2))
    )
    assert invitation is not None
    await invitations.validate(
        invitation.id,
        observed_at=now - timedelta(hours=1),
        is_current_member=True,
    )
    async with session_factory() as session, session.begin():
        campaign = await session.get(type(campaign), campaign_id)
        campaign.state = CampaignState.SETTLING
    bot = QualificationBot(
        {chat_id: "member", (chat_id, participant_user_id): "member", (chat_id, 777): "left"}
    )

    await _handlers(bot, session_factory).handle(_job(str(campaign_id)))

    async with session_factory() as session:
        current = await session.get(Invitation, invitation.id)
        rewards = tuple(await session.scalars(select(TicketLedger.delta)))
    assert current.state is InvitationState.VALID
    assert current.left_at is None
    assert rewards == (1,)
    assert bot.calls == [chat_id]


def _single_invite_bonus_rule() -> CampaignRules:
    return CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(
            retention_hours=1,
            reward_tiers=(InviteRewardTier(invite_count=1, total_bonus=1),),
            max_bonus_tickets=1,
        ),
    )


class FakeStats:
    def __init__(self, totals: dict[int, int]) -> None:
        self._totals = totals
        self.calls: list[tuple[int, tuple[int, ...]]] = []

    async def totals(self, user_id: int, chat_ids: tuple[int, ...]) -> dict[int, int]:
        self.calls.append((user_id, chat_ids))
        return self._totals


async def test_draw_review_marks_ineligible_on_message_threshold_shortfall(
    session_factory,
) -> None:
    rules = CampaignRules(message_thresholds={"-100123": 10})
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(
            session, state=CampaignState.SETTLING, rules=rules
        )
        campaign_id = campaign.id
        participant_id = participant.id
        participant_user_id = participant.user_id
        main_chat_id = group.chat_id
    bot = QualificationBot({main_chat_id: "member"})
    stats = FakeStats({main_chat_id: 3})

    await _handlers(bot, session_factory, stats).handle(_job(str(campaign_id)))

    async with session_factory() as session:
        check = await session.scalar(
            select(QualificationCheck).where(
                QualificationCheck.participant_id == participant_id,
                QualificationCheck.phase == "draw",
            )
        )
        assert check.state is QualificationState.INELIGIBLE
        shortfall = next(
            item
            for item in check.observations["chats"]
            if item.get("error_code") == "message_threshold"
        )
        assert shortfall["chat_id"] == main_chat_id
        assert shortfall["state"] == "ineligible"
        assert shortfall["observed_status"] == "messages 3/10"
    assert stats.calls == [(participant_user_id, (main_chat_id,))]


async def test_draw_review_keeps_eligible_when_message_threshold_met(
    session_factory,
) -> None:
    rules = CampaignRules(message_thresholds={"-100123": 10})
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(
            session, state=CampaignState.SETTLING, rules=rules
        )
        campaign_id = campaign.id
        participant_id = participant.id
        participant_user_id = participant.user_id
        main_chat_id = group.chat_id
    bot = QualificationBot({main_chat_id: "member"})
    stats = FakeStats({main_chat_id: 12})

    await _handlers(bot, session_factory, stats).handle(_job(str(campaign_id)))

    async with session_factory() as session:
        check = await session.scalar(
            select(QualificationCheck).where(
                QualificationCheck.participant_id == participant_id,
                QualificationCheck.phase == "draw",
            )
        )
        assert check.state is QualificationState.ELIGIBLE
        assert [
            item for item in check.observations["chats"] if item.get("error_code")
        ] == []
    assert stats.calls == [(participant_user_id, (main_chat_id,))]


def _handlers(
    bot: QualificationBot, session_factory, stats: FakeStats | None = None
) -> JobHandlers:
    uow_factory = partial(UnitOfWork, session_factory)
    return JobHandlers(
        bot=bot,
        session_factory=session_factory,
        participants=ParticipantService(uow_factory),
        invitations=InvitationService(uow_factory),
        settlement=SettlementService(uow_factory),
        drawing=DrawingService(uow_factory),
        message_stats=stats,
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
