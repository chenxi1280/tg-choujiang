from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.v30_helpers import FIRST, START, invite_link, rules_for, seed_v3, speech
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.domain.enums import InvitationState, TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import DailyProgress, Participant, TicketLedger
from tg_lottery.infrastructure.db.uow import UnitOfWork


def current_rules():
    raw = rules_for("message", ("activity", "invite")).model_dump(mode="json")
    raw["bonus_policy_version"] = "unlimited-v1"
    raw["invite"]["retention_hours"] = 24
    return CampaignRules.model_validate(raw)


async def test_current_activity_keeps_awarding_beyond_daily_one_and_nine_tickets(session_factory):
    _, participant_id = await seed_v3(session_factory, current_rules())
    await speech(session_factory, count=22)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        earned = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(
                TicketLedger.source_type == TicketSource.POPULARITY
            )
        )
        progress = await session.scalar(select(DailyProgress))
        assert participant.admission_qualified
        assert earned == progress.tickets_awarded == 11
        assert participant.ticket_total_cache == 12


async def confirm_invitee(service, *, link_id, user_id, joined):
    candidate = await service.record_joined(
        JoinObservation(link_id, user_id, joined, chat_id=FIRST)
    )
    due = joined + timedelta(hours=24)
    await service.begin_validation(candidate.id, observed_at=due)
    confirmed = await service.complete_validation(
        candidate.id, observed_at=due, is_current_member=True
    )
    assert confirmed.state is InvitationState.VALID
    return confirmed


async def test_current_invites_wait_24_hours_and_repeat_join_never_adds_another_reward(
    session_factory,
):
    campaign_id, participant_id = await seed_v3(session_factory, current_rules())
    link_id = await invite_link(session_factory, campaign_id, participant_id)
    service = InvitationService(partial(UnitOfWork, session_factory))
    joined = START + timedelta(minutes=1)
    candidate = await service.record_joined(JoinObservation(link_id, 301, joined, chat_id=FIRST))
    due = joined + timedelta(hours=24)
    with pytest.raises(ValueError, match="retention period"):
        await service.begin_validation(candidate.id, observed_at=due - timedelta(seconds=1))
    confirmed = await confirm_invitee(service, link_id=link_id, user_id=301, joined=joined)
    await service.record_left(
        campaign_id,
        301,
        due + timedelta(minutes=1),
        participation_group_id=confirmed.participation_group_id,
    )
    await service.record_rejoined(
        campaign_id,
        301,
        due + timedelta(minutes=2),
        participation_group_id=confirmed.participation_group_id,
    )
    await service.record_joined(
        JoinObservation(link_id, 301, due + timedelta(minutes=3), chat_id=FIRST)
    )
    for number in range(2, 12):
        await confirm_invitee(
            service,
            link_id=link_id,
            user_id=300 + number,
            joined=START + timedelta(minutes=number),
        )
    async with session_factory() as session:
        earned = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(
                TicketLedger.source_type == TicketSource.INVITATION
            )
        )
        participant = await session.get(Participant, participant_id)
        assert earned == 11
        assert participant.ticket_total_cache == 12
