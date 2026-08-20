import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.invitation_rewards import add_invite_bonus
from tg_lottery.domain.enums import InvitationState
from tg_lottery.infrastructure.db import Campaign, Invitation, InviteLink, Participant, TicketLedger


async def test_parallel_invite_rewards_preserve_participant_ticket_cache(
    session_factory,
) -> None:
    campaign_id, participant_id, invitation_ids = await _seed_invitations(session_factory)
    started = asyncio.Event()

    async with session_factory() as first:
        await first.begin()
        campaign = await first.get(Campaign, campaign_id)
        invitation = await first.get(Invitation, invitation_ids[0])
        assert campaign is not None and invitation is not None
        await add_invite_bonus(
            first,
            invitation=invitation,
            campaign=campaign,
            reward=1,
            business_key="parallel-invite-1",
            occurred_at=datetime.now(UTC),
        )
        second = asyncio.create_task(
            _award_second(
                session_factory,
                invitation_id=invitation_ids[1],
                campaign_id=campaign_id,
                started=started,
            )
        )
        await started.wait()
        await asyncio.sleep(0.01)
        assert second.done() is False
        await first.commit()
    await asyncio.wait_for(second, timeout=1)

    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        ledger_total = await session.scalar(select(func.sum(TicketLedger.delta)))
    assert participant is not None and participant.ticket_total_cache == 2
    assert ledger_total == 2


async def _award_second(
    session_factory, *, invitation_id, campaign_id, started: asyncio.Event
) -> None:
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        invitation = await session.get(Invitation, invitation_id)
        assert campaign is not None and invitation is not None
        started.set()
        await add_invite_bonus(
            session,
            invitation=invitation,
            campaign=campaign,
            reward=1,
            business_key="parallel-invite-2",
            occurred_at=datetime.now(UTC),
        )


async def _seed_invitations(session_factory):
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash="c" * 64,
            telegram_name="parallel_invites",
            expire_at=now + timedelta(days=1),
        )
        session.add(link)
        await session.flush()
        invitations = [
            Invitation(
                campaign_id=campaign.id,
                invite_link_id=link.id,
                inviter_participant_id=participant.id,
                invitee_user_id=user_id,
                state=InvitationState.VALID,
                candidate_at=now,
            )
            for user_id in (301, 302)
        ]
        session.add_all(invitations)
        await session.flush()
        return campaign.id, participant.id, tuple(item.id for item in invitations)
