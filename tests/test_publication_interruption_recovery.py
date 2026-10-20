"""Recover the committed campaign after the session checkpoint fails."""

from functools import partial
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.test_create_entry_incident import PANEL_ID, USER_ID, _query
from tests.test_preview_publish_continuity import _confirmation
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, Group, GroupAdmin, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.creator_publication import CreatorPublisher


@pytest.mark.parametrize("entry", ["confirm", "publish"])
async def test_committed_campaign_recovers_after_checkpoint_failure(
    session_factory, monkeypatch, entry
):
    sessions, _, _, data = await _confirmation(session_factory)
    async with session_factory() as session, session.begin():
        group = Group(chat_id=-1001, title="恢复测试群", timezone="Asia/Shanghai")
        session.add(group)
        await session.flush()
        session.add(GroupAdmin(group_id=group.id, user_id=USER_ID, source="owner", active=True))
    publisher = CreatorPublisher(
        bot=object(),
        bot_username="lottery_test_bot",
        sessions=sessions,
        campaigns=CampaignService(partial(UnitOfWork, session_factory)),
        queries=QueryService(session_factory),
        profiles=object(),
        renderer=object(),
    )
    monkeypatch.setattr(publisher, "_resolve", AsyncMock(return_value=data))
    saved_set = sessions.set
    monkeypatch.setattr(sessions, "set", AsyncMock(side_effect=RuntimeError("checkpoint failed")))
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        await publisher.publish(_query(), None)
    monkeypatch.setattr(sessions, "set", saved_set)
    stored = await sessions.get(USER_ID)
    assert "published_campaign_id" not in stored.data and stored.state == "draft:publish_confirm"
    async with session_factory() as session:
        original = (await session.scalars(select(Campaign))).one()
        assert not (await session.scalars(select(TelegramOutbox))).all()
    await sessions.issue_panel_context(USER_ID, PANEL_ID)
    assert (await sessions.get(USER_ID)).data["draft_revision"] > original.source_draft_revision
    permission = AsyncMock(side_effect=UserFacingError("权限已丢失"))
    monkeypatch.setattr("tg_lottery.telegram.creator_publication.verify_campaign_admin", permission)
    monkeypatch.setattr("tg_lottery.telegram.creator_publication.edit_panel", AsyncMock())
    with pytest.raises(UserFacingError, match="权限已丢失"):
        await getattr(publisher, entry)(_query(), None)
    async with session_factory() as session:
        assert not (await session.scalars(select(TelegramOutbox))).all()
    permission.side_effect = None
    await getattr(publisher, entry)(_query(), None)
    await getattr(publisher, entry)(_query(), None)
    async with session_factory() as session:
        recovered = (await session.scalars(select(Campaign))).one()
        outbox = (await session.scalars(select(TelegramOutbox))).one()
        assert recovered.id == original.id
        assert recovered.source_draft_revision == original.source_draft_revision
        assert outbox.aggregate_id == str(original.id)
        assert outbox.operation == "send_campaign_card"
    assert (await sessions.get(USER_ID)).data["published_campaign_id"] == str(original.id)

    await _assert_running_unchanged(session_factory, publisher, entry=entry)


async def _assert_running_unchanged(session_factory, publisher, *, entry):
    campaign_id = (await publisher._sessions.get(USER_ID)).data["published_campaign_id"]
    from uuid import UUID

    campaign_id = UUID(campaign_id)
    async with session_factory() as session, session.begin():
        running = await session.get(Campaign, campaign_id)
        running.state = CampaignState.RUNNING
        frozen = (running.rules, running.draw_at, running.cutoff_at, running.presentation)
    await getattr(publisher, entry)(_query(), None)
    async with session_factory() as session:
        running = await session.get(Campaign, campaign_id)
        assert running.state == CampaignState.RUNNING
        assert (running.rules, running.draw_at, running.cutoff_at, running.presentation) == frozen
        assert len((await session.scalars(select(TelegramOutbox))).all()) == 1
