from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256

from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.invitations import InvitationService, JoinObservation
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Invitation, InviteLink
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_old_campaign_link_accepts_no_attribution_after_cutoff(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session,
            rules=CampaignRules(bonus_types=("invite",)),
            cutoff_at=now - timedelta(seconds=1),
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash=sha256(b"expired-link").hexdigest(),
            telegram_name="lot_expired",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        link_id = link.id

    invitation = await InvitationService(partial(UnitOfWork, session_factory)).record_joined(
        JoinObservation(link_id, 399, now)
    )

    assert invitation is None
    async with session_factory() as session:
        assert await session.scalar(select(func.count(Invitation.id))) == 0
