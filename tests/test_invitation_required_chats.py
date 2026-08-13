from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256

from tests.helpers import seed_campaign
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import InvitationState
from tg_lottery.domain.rules import CampaignRules, InviteRule
from tg_lottery.infrastructure.db import InviteLink
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_invitation_events_are_routed_from_an_extra_required_chat(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session,
            rules=CampaignRules(required_chat_ids=(-1002,)),
            cutoff_at=now + timedelta(days=2),
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"extra-chat-link").hexdigest(),
            telegram_name="lot_extra_chat",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        link_id = link.id
    invitation = await InvitationService(partial(UnitOfWork, session_factory)).record_joined(
        JoinObservation(link_id, 305, now)
    )
    assert invitation is not None

    routed = await QueryService(session_factory).invitations_for_chat_user(-1002, 305)

    assert [item.id for item in routed] == [invitation.id]


async def test_failed_membership_validation_can_recover_after_joining_missing_chat(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("invite",),
        invite=InviteRule(retention_hours=1),
        required_chat_ids=(-1002,),
    )
    link_id, campaign_id = await _seed_invite_link(session_factory, rules, now)
    service = InvitationService(partial(UnitOfWork, session_factory))
    invitation = await service.record_joined(JoinObservation(link_id, 304, now))
    assert invitation is not None
    failed = await service.validate(
        invitation.id,
        observed_at=now + timedelta(hours=1),
        is_current_member=False,
    )
    rejoined_at = now + timedelta(hours=1, minutes=5)

    recovered = await service.record_rejoined(campaign_id, 304, rejoined_at)

    assert failed.state is InvitationState.LEFT_BEFORE_VALID
    assert failed.validation_error == "membership_ineligible"
    assert recovered is not None
    assert recovered.state is InvitationState.JOINED_PENDING
    assert recovered.due_at == rejoined_at + timedelta(hours=1)


async def _seed_invite_link(session_factory, rules, now):
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session, rules=rules, cutoff_at=now + timedelta(days=1)
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"recover-extra-chat-link").hexdigest(),
            telegram_name="lot_recover_chat",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        return link.id, campaign.id
