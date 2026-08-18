from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.campaigns import CampaignService, DraftSource, PrizeSpec
from tg_lottery.application.records import add_outbox
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignDraft
from tg_lottery.infrastructure.db import (
    CampaignParticipationGroup,
    Group,
    GroupAdmin,
    Prize,
    RuleRevision,
    TelegramOutbox,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_draft_publication_source_reuses_one_campaign(session_factory) -> None:
    async with session_factory() as session, session.begin():
        group = Group(chat_id=-100500, title="发布幂等群", timezone="Asia/Shanghai")
        session.add(group)
        await session.flush()
        session.add(GroupAdmin(group_id=group.id, user_id=201, source="owner", active=True))
    service = CampaignService(partial(UnitOfWork, session_factory))
    now = datetime.now(UTC)
    draft = CampaignDraft(
        group_id=-100500,
        name="同一草稿",
        preheat_at=now,
        cutoff_at=now + timedelta(hours=1),
        draw_at=now + timedelta(hours=2),
    )
    prizes = (PrizeSpec("一等奖", 1, "联系管理员"),)
    source = DraftSource("stable-draft", 4)

    first = await service.create_draft(draft, prizes, 201, source=source)
    second = await service.create_draft(draft, prizes, 201, source=source)
    await service.publish(first.id, 201, "lottery_test_bot")
    await service.publish(second.id, 201, "lottery_test_bot")

    async with session_factory() as session:
        assert first.id == second.id
        assert await session.scalar(select(func.count(RuleRevision.id))) == 1
        assert await session.scalar(select(func.count(Prize.id))) == 1
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 1


async def test_draft_publication_source_rejects_changed_revision(session_factory) -> None:
    async with session_factory() as session, session.begin():
        group = Group(chat_id=-100501, title="发布冲突群", timezone="Asia/Shanghai")
        session.add(group)
        await session.flush()
        session.add(GroupAdmin(group_id=group.id, user_id=201, source="owner", active=True))
    service = CampaignService(partial(UnitOfWork, session_factory))
    now = datetime.now(UTC)
    draft = CampaignDraft(
        group_id=-100501,
        name="冲突草稿",
        preheat_at=now,
        cutoff_at=now + timedelta(hours=1),
        draw_at=now + timedelta(hours=2),
    )
    prizes = (PrizeSpec("一等奖", 1, "联系管理员"),)
    await service.create_draft(draft, prizes, 201, source=DraftSource("draft-x", 1))

    with pytest.raises(ValueError, match="conflicts"):
        await service.create_draft(draft, prizes, 201, source=DraftSource("draft-x", 2))


async def test_publication_retry_only_requeues_explicit_failures(session_factory) -> None:
    async with session_factory() as session, session.begin():
        primary, campaign, _ = await seed_campaign(session, state=CampaignState.PUBLISHING)
        secondary = Group(chat_id=-100502, title="未知群", timezone="Asia/Shanghai")
        session.add(secondary)
        await session.flush()
        session.add(GroupAdmin(group_id=secondary.id, user_id=101, source="owner", active=True))
        failed_target = _publication_target(campaign.id, primary, 1, "failed")
        unknown_target = _publication_target(campaign.id, secondary, 2, "unknown")
        session.add_all((failed_target, unknown_target))
        failed = _publication_outbox(session, campaign.id, primary.chat_id, "failed")
        unknown = _publication_outbox(session, campaign.id, secondary.chat_id, "unknown")
        failed.state = OutboxState.PRE_ACCEPT_FAILED
        unknown.state = OutboxState.UNKNOWN_HOLD
        campaign_id = campaign.id
    service = CampaignService(partial(UnitOfWork, session_factory))

    assert await service.retry_failed_publication(campaign_id, 101) == 1

    async with session_factory() as session:
        rows = {
            row.recipient_chat_id: row.state
            for row in await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.aggregate_id == str(campaign_id))
            )
        }
        targets = {
            row.chat_id_snapshot: row.publication_state
            for row in await session.scalars(
                select(CampaignParticipationGroup).where(
                    CampaignParticipationGroup.campaign_id == campaign_id
                )
            )
        }
    assert rows == {
        primary.chat_id: OutboxState.PENDING,
        secondary.chat_id: OutboxState.UNKNOWN_HOLD,
    }
    assert targets == {primary.chat_id: "pending", secondary.chat_id: "unknown"}
    with pytest.raises(UserFacingError, match="没有可安全重试"):
        await service.retry_failed_publication(campaign_id, 101)


def _publication_target(campaign_id, group, position: int, state: str):
    return CampaignParticipationGroup(
        campaign_id=campaign_id,
        group_id=group.id,
        position=position,
        is_primary=position == 1,
        chat_id_snapshot=group.chat_id,
        title_snapshot=group.title,
        publication_state=state,
    )


def _publication_outbox(session, campaign_id, chat_id: int, suffix: str):
    return add_outbox(
        session,
        operation="send_campaign_card",
        aggregate_type="campaign",
        aggregate_id=str(campaign_id),
        recipient_chat_id=chat_id,
        business_key=f"publication-retry:{campaign_id}:{suffix}",
        payload={"campaign_id": str(campaign_id)},
    )
