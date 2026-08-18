from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tests.v22_bonus_test_helpers import draft_data as _draft_data
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.invitation_models import JoinObservation
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import OutboxState
from tg_lottery.domain.errors import InvitationValidationDeferred
from tg_lottery.domain.rules import (
    CampaignRules,
    GroupActivityRule,
    GroupInviteRule,
    InviteRule,
)
from tg_lottery.infrastructure.db import (
    CampaignOperationalBlock,
    CampaignParticipationGroup,
    DailyProgress,
    Group,
    InviteLink,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.draft_config import rules_from_data


def test_draft_config_freezes_group_rule_maps() -> None:
    data = _draft_data() | {
        "timezone": "Asia/Shanghai",
        "bonus_types": ["invite", "activity"],
        "group_invite_rules": {"-1001": GroupInviteRule().model_dump(mode="json")},
        "group_activity_rules": {
            "-1002": GroupActivityRule(
                daily_cap_mode="unlimited", daily_max_cycles=None
            ).model_dump(mode="json")
        },
    }

    rules = rules_from_data(data)

    assert rules.invite_rule_for(-1001).invites_per_cycle == 2
    assert rules.activity_rule_for(-1002).daily_cap_mode == "unlimited"


async def test_activity_service_awards_each_group_independently(session_factory) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("activity",),
        group_activity_rules={
            "-100123": GroupActivityRule(
                messages_per_cycle=2,
                min_text_length=20,
                daily_cap_mode="limited",
                daily_max_cycles=1,
            ),
            "-100124": GroupActivityRule(
                messages_per_cycle=3,
                min_text_length=5,
                daily_cap_mode="unlimited",
                daily_max_cycles=None,
            ),
        },
    )
    campaign_id, participant_id = await _seed_activity_campaign(session_factory, rules, now)
    service = ActivityService(partial(UnitOfWork, session_factory))
    await _record_messages(service, chat_id=-100123, count=4, now=now, minimum_length=20)
    await _record_messages(service, chat_id=-100124, count=6, now=now, minimum_length=5)

    async with session_factory() as session:
        progress = (await session.scalars(select(DailyProgress))).all()
        total = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(
                TicketLedger.participant_id == participant_id
            )
        )
    assert sorted((item.valid_message_count, item.tickets_awarded) for item in progress) == [
        (4, 1),
        (6, 2),
    ]
    assert all(item.participation_group_id is not None for item in progress)
    assert total == 3
    bonus = await QueryService(session_factory).group_bonus_progress(campaign_id, 201)
    assert bonus is not None
    assert sorted(
        (item.chat_id, item.valid_messages, item.awarded_tickets) for item in bonus.activities
    ) == [(-100124, 6, 2), (-100123, 4, 1)]


async def test_invitation_service_attributes_and_caps_each_group(session_factory) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(mode="ratio", retention_hours=1, valid_invites_per_ticket=2),
        group_invite_rules={
            "-100123": GroupInviteRule(invites_per_cycle=2),
            "-100124": GroupInviteRule(
                invites_per_cycle=3,
                cap_mode="limited",
                max_bonus_tickets=1,
            ),
        },
    )
    link_ids, participant_id, campaign_id = await _seed_invitation_campaign(
        session_factory, rules, now
    )
    service = InvitationService(partial(UnitOfWork, session_factory))
    await _validate_group_invites(
        service,
        link_id=link_ids[0],
        chat_id=-100123,
        user_ids=range(1001, 1005),
        now=now,
    )
    await _validate_group_invites(
        service,
        link_id=link_ids[1],
        chat_id=-100124,
        user_ids=range(2001, 2007),
        now=now,
    )

    totals = await _group_invite_totals(session_factory, participant_id)
    assert sorted(totals.values()) == [1, 2]
    bonus = await QueryService(session_factory).group_bonus_progress(campaign_id, 201)
    assert bonus is not None
    assert sorted(
        (item.chat_id, item.valid_invites, item.awarded_tickets) for item in bonus.invitations
    ) == [(-100124, 6, 1), (-100123, 4, 2)]


async def test_failed_group_invite_link_request_can_be_retried_safely(session_factory) -> None:
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(retention_hours=1),
        group_invite_rules={"-100123": GroupInviteRule(invites_per_cycle=2)},
    )
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session, rules=rules)
        target = _target(campaign.id, group, position=1, primary=True)
        session.add(target)
        await session.flush()
        campaign_id = campaign.id
        participant_id = participant.id
    service = ParticipantService(
        partial(UnitOfWork, session_factory),
        invite_name_factory=lambda: "lottery_retry_test",
    )
    await service.request_group_invite_link(
        campaign_id, 201, -100123, bot_username="lottery_test_bot"
    )
    async with session_factory() as session, session.begin():
        outbox = await session.scalar(
            select(TelegramOutbox).where(
                TelegramOutbox.aggregate_id == str(participant_id),
                TelegramOutbox.operation == "create_invite_link",
            )
        )
        outbox.state = OutboxState.PRE_ACCEPT_FAILED
        outbox.last_error = "explicit Telegram rejection"

    statuses = await QueryService(session_factory).group_invite_link_statuses(campaign_id, 201)
    assert statuses[-100123].delivery_state is OutboxState.PRE_ACCEPT_FAILED

    await service.request_group_invite_link(
        campaign_id, 201, -100123, bot_username="lottery_test_bot"
    )

    statuses = await QueryService(session_factory).group_invite_link_statuses(campaign_id, 201)
    assert statuses[-100123].delivery_state is OutboxState.PENDING


async def test_one_group_block_does_not_pause_other_group_invite_validation(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(invite=InviteRule(retention_hours=1))
    async with session_factory() as session, session.begin():
        first, campaign, participant = await seed_campaign(session, rules=rules)
        second = Group(chat_id=-100124, title="B群", timezone="Asia/Shanghai")
        session.add(second)
        await session.flush()
        first_target = _target(campaign.id, first, position=1, primary=True)
        second_target = _target(campaign.id, second, position=2, primary=False)
        session.add_all((first_target, second_target))
        await session.flush()
        first_link = _link(
            campaign, participant_id=participant.id, target_id=first_target.id, suffix="blocked"
        )
        second_link = _link(
            campaign, participant_id=participant.id, target_id=second_target.id, suffix="open"
        )
        session.add_all((first_link, second_link))
        session.add(
            CampaignOperationalBlock(
                campaign_id=campaign.id,
                target_kind="participation_group",
                chat_id=first.chat_id,
                error_code="permission_unknown",
                first_detected_at=now,
                last_observed_at=now,
            )
        )
        await session.flush()
        link_ids = (first_link.id, second_link.id)
    service = InvitationService(partial(UnitOfWork, session_factory))
    blocked = await service.record_joined(JoinObservation(link_ids[0], 3001, now))
    available = await service.record_joined(JoinObservation(link_ids[1], 3002, now))

    with pytest.raises(InvitationValidationDeferred):
        await service.begin_validation(blocked.id, observed_at=now + timedelta(hours=1))
    attempt = await service.begin_validation(available.id, observed_at=now + timedelta(hours=1))
    assert attempt.chat_ids == (-100124,)


async def _seed_activity_campaign(session_factory, rules: CampaignRules, now: datetime):
    async with session_factory() as session, session.begin():
        first, campaign, participant = await seed_campaign(
            session, rules=rules, cutoff_at=now + timedelta(days=2)
        )
        second = Group(chat_id=-100124, title="B群", timezone="Asia/Shanghai")
        session.add(second)
        await session.flush()
        session.add_all(
            (
                _target(campaign.id, first, position=1, primary=True),
                _target(campaign.id, second, position=2, primary=False),
            )
        )
        campaign.presentation = {
            "participation_groups": [
                {"id": first.chat_id, "title": first.title},
                {"id": second.chat_id, "title": second.title},
            ]
        }
        return campaign.id, participant.id


async def _group_invite_totals(session_factory, participant_id) -> dict[str, int]:
    async with session_factory() as session:
        rows = await session.execute(
            select(
                TicketLedger.participation_group_id,
                func.sum(TicketLedger.delta),
            )
            .where(TicketLedger.participant_id == participant_id)
            .group_by(TicketLedger.participation_group_id)
        )
    return {str(group_id): int(value) for group_id, value in rows}


async def _seed_invitation_campaign(session_factory, rules: CampaignRules, now: datetime):
    async with session_factory() as session, session.begin():
        first, campaign, participant = await seed_campaign(
            session, rules=rules, cutoff_at=now + timedelta(days=3)
        )
        second = Group(chat_id=-100124, title="B群", timezone="Asia/Shanghai")
        session.add(second)
        await session.flush()
        first_target = _target(campaign.id, first, position=1, primary=True)
        second_target = _target(campaign.id, second, position=2, primary=False)
        session.add_all((first_target, second_target))
        await session.flush()
        links = (
            _link(campaign, participant_id=participant.id, target_id=first_target.id, suffix="a"),
            _link(campaign, participant_id=participant.id, target_id=second_target.id, suffix="b"),
        )
        session.add_all(links)
        await session.flush()
        return tuple(link.id for link in links), participant.id, campaign.id


def _target(campaign_id, group, *, position: int, primary: bool):
    return CampaignParticipationGroup(
        campaign_id=campaign_id,
        group_id=group.id,
        position=position,
        is_primary=primary,
        chat_id_snapshot=group.chat_id,
        title_snapshot=group.title,
        private=False,
    )


def _link(campaign, *, participant_id, target_id, suffix: str) -> InviteLink:
    return InviteLink(
        campaign_id=campaign.id,
        participant_id=participant_id,
        participation_group_id=target_id,
        link_hash=sha256(f"group-{suffix}".encode()).hexdigest(),
        telegram_name=f"lot_group_{suffix}",
        expire_at=campaign.cutoff_at,
    )


async def _record_messages(
    service: ActivityService,
    *,
    chat_id: int,
    count: int,
    now: datetime,
    minimum_length: int,
) -> None:
    for index in range(count):
        await service.record_message(
            MessageObservation(
                chat_id=chat_id,
                message_id=abs(chat_id) + index,
                user_id=201,
                event_at=now + timedelta(seconds=index),
                text=("有效内容" * minimum_length) + str(index),
            )
        )


async def _validate_group_invites(
    service: InvitationService,
    *,
    link_id,
    chat_id: int,
    user_ids,
    now: datetime,
) -> None:
    for offset, user_id in enumerate(user_ids):
        joined_at = now + timedelta(minutes=offset)
        invitation = await service.record_joined(
            JoinObservation(link_id, user_id, joined_at, chat_id=chat_id)
        )
        assert invitation is not None
        await service.validate(
            invitation.id,
            observed_at=joined_at + timedelta(hours=1),
            is_current_member=True,
        )
