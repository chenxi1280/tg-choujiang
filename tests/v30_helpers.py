from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256

from sqlalchemy import select

from tests.test_v22_group_bonus_integration import _link, _seed_activity_campaign
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.domain.admission import InviteAdmission, InviteAdmissionRule, MessageAdmission
from tg_lottery.domain.enums import QualificationState, TicketSource
from tg_lottery.domain.rules import CampaignRules, GroupActivityRule, GroupInviteRule, InviteRule
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Participant,
    QualificationCheck,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork

START = datetime(2026, 10, 4, 10, tzinfo=UTC)
FIRST = -100123
SECOND = -100124


def rules_for(mode, bonuses=(), *, groups=None):
    gate = (
        MessageAdmission(type="message", groups=groups or {str(FIRST): dict(daily_messages=2)})
        if mode == "message"
        else InviteAdmission(
            type="invite",
            groups=groups or {str(FIRST): InviteAdmissionRule(required_valid_invites=2)},
        )
    )
    return CampaignRules(
        participation_rules_version="v3.0",
        admission=gate,
        bonus_types=bonuses,
        invite=InviteRule(mode="ratio", valid_invites_per_ticket=1, retention_hours=0),
        group_activity_rules={
            str(FIRST): GroupActivityRule(
                messages_per_cycle=2,
                min_text_length=4,
                daily_cap_mode="unlimited",
                daily_max_cycles=None,
            )
        },
        group_invite_rules={str(FIRST): GroupInviteRule(invites_per_cycle=1)},
    )


async def seed_v3(factory, rules):
    campaign_id, participant_id = await _seed_activity_campaign(factory, rules, START)
    async with factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.preheat_at = START - timedelta(hours=1)
        campaign.cutoff_at = START + timedelta(days=4)
        campaign.draw_at = campaign.cutoff_at + timedelta(minutes=5)
        participant = await session.get(Participant, participant_id)
        participant.admission_qualified = False
        participant.counting_started_at = START
        participant.counting_started_update_id = 100
        participant.registration_intent_at = START
        participant.ticket_total_cache = 1
        session.add(
            QualificationCheck(
                participant_id=participant_id,
                phase="registration",
                state=QualificationState.ELIGIBLE,
                observed_at=START,
                request_hash=sha256(b"registration").hexdigest(),
                source_update_id=100,
            )
        )
        session.add(
            TicketLedger(
                campaign_id=campaign_id,
                participant_id=participant_id,
                delta=1,
                source_type=TicketSource.REGISTRATION,
                source_ref=str(participant_id),
                business_key=f"registration:{participant_id}",
                occurred_at=START,
            )
        )
    return campaign_id, participant_id


async def speech(factory, *, chat_id=FIRST, count=2, at=START, first_id=1):
    service = ActivityService(partial(UnitOfWork, factory))
    for number in range(count):
        observation = MessageObservation(
            chat_id=chat_id,
            message_id=first_id + number,
            user_id=201,
            event_at=at + timedelta(seconds=number + 1),
            text=f"第{first_id + number}条真实有效消息内容",
        )
        await service.record_message(observation)
        await service.record_message(observation)


async def invite_link(factory, campaign_id, participant_id, *, chat_id=FIRST):
    async with factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        target = await session.scalar(
            select(CampaignParticipationGroup).where(
                CampaignParticipationGroup.campaign_id == campaign_id,
                CampaignParticipationGroup.chat_id_snapshot == chat_id,
            )
        )
        link = _link(
            campaign, participant_id=participant_id, target_id=target.id, suffix=str(chat_id)
        )
        session.add(link)
        await session.flush()
        return link.id


async def prepare_draw(factory, campaign_id, participant_id):
    from tg_lottery.infrastructure.db import PollingOffset

    async with factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        now = campaign.cutoff_at + timedelta(minutes=10)
        campaign.contract_version = "v2.4"
        targets = list(
            await session.scalars(
                select(CampaignParticipationGroup).where(
                    CampaignParticipationGroup.campaign_id == campaign_id
                )
            )
        )
        for index, target in enumerate(targets):
            target.publication_state = "confirmed"
            target.published_message_id = 800 + index
        session.add(
            PollingOffset(
                bot_id=123, last_durable_update_id=50, last_poll_succeeded_at=now, updated_at=now
            )
        )
        session.add(
            QualificationCheck(
                participant_id=participant_id,
                phase="draw",
                state=QualificationState.ELIGIBLE,
                observed_at=now,
                request_hash="d" * 64,
            )
        )
    return now
