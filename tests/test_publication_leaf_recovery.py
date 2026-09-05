from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tests.test_multi_group_publication_projection import (
    _confirm_invites_and_project,
    _published_private_campaign,
)
from tests.test_required_chat_publication import _cipher, _seed_private_publication
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.publication_queries import load_publication_issues
from tg_lottery.application.records import utcnow
from tg_lottery.application.required_chat_links import project_required_chat_invite
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, CampaignRequirement, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.gateway import GatewayResult


async def test_failed_private_condition_is_named_and_retried(session_factory) -> None:
    campaign_id = await _seed_private_publication(session_factory)
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(TelegramOutbox))
        row.state, row.last_error = OutboxState.PRE_ACCEPT_FAILED, "not enough rights"
        row.recipient_chat_id = -1008877
        session.add(
            CampaignRequirement(
                campaign_id=campaign_id,
                chat_id_snapshot=-1008877,
                position=1,
                chat_type="channel",
                title="验收说明频道",
                private=True,
            )
        )
    issues = await load_publication_issues(session_factory, campaign_id)
    assert len(issues) == 1
    assert issues[0].title == "验收说明频道"
    assert issues[0].operation == "create_required_chat_invite"
    service = CampaignService(partial(UnitOfWork, session_factory))
    assert await service.retry_failed_publication(campaign_id, 101) == 1
    async with session_factory() as session:
        assert (await session.scalar(select(TelegramOutbox))).state is OutboxState.PENDING


async def test_expired_publication_cannot_be_replayed(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=CampaignState.PUBLISHING)
        campaign.cutoff_at = utcnow() - timedelta(minutes=1)
        campaign_id = campaign.id
    service = CampaignService(partial(UnitOfWork, session_factory))
    with pytest.raises(UserFacingError, match="已过截止时间"):
        await service.retry_failed_publication(campaign_id, 101)


@pytest.mark.parametrize("multi", [False, True])
async def test_repeated_private_link_projection_keeps_one_card_per_group(session_factory, multi):
    cipher = _cipher()
    if multi:
        campaign = await _published_private_campaign(session_factory)
        await _confirm_invites_and_project(session_factory, campaign)
        campaign_id = campaign.id
    else:
        campaign_id = await _seed_private_publication(session_factory)
    for _ in range(2):
        async with session_factory() as session, session.begin():
            row = await session.scalar(
                select(TelegramOutbox).where(
                    TelegramOutbox.operation == "create_required_chat_invite",
                )
            )
            await project_required_chat_invite(
                session,
                row,
                GatewayResult({}, invite_link="https://t.me/+one"),
                cipher=cipher,
            )
    async with session_factory() as session:
        cards = tuple(
            await session.scalars(
                select(TelegramOutbox).where(
                    TelegramOutbox.operation == "send_campaign_card",
                )
            )
        )
        campaign = await session.get(Campaign, campaign_id)
    assert len(cards) == (2 if multi else 1)
    assert len({row.recipient_chat_id for row in cards}) == len(cards)
    assert campaign.state is CampaignState.PUBLISHING
