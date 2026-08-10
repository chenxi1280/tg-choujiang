from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tg_lottery.application.campaigns import CampaignService, DraftSource, PrizeSpec
from tg_lottery.domain.rules import CampaignDraft
from tg_lottery.infrastructure.db import Group, GroupAdmin, Prize, RuleRevision, TelegramOutbox
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
