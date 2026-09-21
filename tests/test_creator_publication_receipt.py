from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_creator_settings import _panel_query
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_publication import CreatorPublisher


def publisher_fixture():
    sessions = SimpleNamespace(set=AsyncMock(), clear=AsyncMock())
    publisher = CreatorPublisher(
        bot=object(),
        bot_username="test_bot",
        sessions=sessions,
        campaigns=SimpleNamespace(publish=AsyncMock()),
        queries=SimpleNamespace(
            campaign=AsyncMock(
                return_value=SimpleNamespace(
                    name="活动",
                    public_ref="abc",
                )
            )
        ),
        profiles=SimpleNamespace(save_last_draft=AsyncMock()),
        renderer=object(),
    )
    return publisher, sessions


async def test_finish_edits_original_panel_with_forwardable_link_before_clearing(monkeypatch):
    publisher, sessions = publisher_fixture()
    edit = AsyncMock()
    monkeypatch.setattr(
        "tg_lottery.telegram.creator_publication._message",
        lambda _: SimpleNamespace(edit_text=edit),
    )
    await publisher._finish(_panel_query(), {}, uuid4())
    text = edit.await_args.args[0]
    keyboard = edit.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].url in text
    assert "join_abc" in text
    sessions.clear.assert_awaited_once_with(201)


async def test_receipt_delivery_failure_retains_publication_session_for_retry(monkeypatch):
    publisher, sessions = publisher_fixture()
    edit = AsyncMock(side_effect=RuntimeError("delivery failed"))
    monkeypatch.setattr(
        "tg_lottery.telegram.creator_publication._message",
        lambda _: SimpleNamespace(edit_text=edit),
    )
    with pytest.raises(RuntimeError, match="delivery failed"):
        await publisher._finish(_panel_query(), {}, uuid4())
    sessions.clear.assert_not_awaited()
    assert sessions.set.await_args.args[1] == "draft:publishing"


async def test_missing_created_campaign_is_explicit_and_does_not_clear_session():
    publisher, sessions = publisher_fixture()
    publisher._queries.campaign.return_value = None
    with pytest.raises(UserFacingError, match="找不到已创建的活动"):
        await publisher._finish(_panel_query(), {}, uuid4())
    sessions.clear.assert_not_awaited()
