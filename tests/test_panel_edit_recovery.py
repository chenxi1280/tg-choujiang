from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import select

from tests.test_inbox_ordering import _processor, _stored_update
from tests.test_personal_flow_messages import PersonalQueries
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import TelegramUpdate
from tg_lottery.telegram.personal_flow import PersonalFlow


class RefreshThenStart:
    def __init__(self):
        self.flow = PersonalFlow(profiles=None, queries=PersonalQueries(), bot_username="test_bot")

    async def handle(self, update):
        if update.message.text == "/start":
            await self.flow.show_index(update.message)
        else:
            await self.flow._show_created(update.message, "active", edit=True)


class PanelBot:
    def __init__(self, error):
        self.error = error
        self.methods = []

    async def __call__(self, method, request_timeout=None):
        self.methods.append(method)
        if isinstance(method, EditMessageText):
            raise self.error
        return object()


async def _queued_pair(session_factory):
    refresh = _stored_update(100)
    refresh.payload["message"]["text"] = "refresh"
    async with session_factory() as session, session.begin():
        session.add_all([refresh, _stored_update(101)])


async def test_unchanged_list_edit_completes_and_unblocks_next_start(session_factory):
    await _queued_pair(session_factory)
    method = EditMessageText(chat_id=201, message_id=100, text="existing panel")
    bot = PanelBot(
        TelegramBadRequest(
            method=method,
            message=(
                "Bad Request: message is not modified: specified new message content "
                "and reply markup are exactly the same"
            ),
        )
    )
    processor = _processor(RefreshThenStart(), session_factory, bot=bot)
    assert await processor.process_once()
    assert await processor.process_once()
    async with session_factory() as session:
        rows = (
            await session.scalars(select(TelegramUpdate).order_by(TelegramUpdate.update_id))
        ).all()
    assert [row.state for row in rows] == [UpdateState.PROCESSED, UpdateState.PROCESSED]
    assert all(row.attempts == 1 and row.last_error is None for row in rows)
    assert isinstance(bot.methods[-1], SendMessage)
    assert "抽奖助手" in bot.methods[-1].text


@pytest.mark.parametrize(
    "error_text", ["Bad Request: message to edit not found", "Bad Request: can't parse entities"]
)
async def test_other_edit_rejections_remain_failed_and_keep_order(session_factory, error_text):
    await _queued_pair(session_factory)
    method = EditMessageText(chat_id=201, message_id=100, text="panel")
    bot = PanelBot(TelegramBadRequest(method=method, message=error_text))
    processor = _processor(RefreshThenStart(), session_factory, bot=bot)
    assert await processor.process_once()
    assert not await processor.process_once()
    async with session_factory() as session:
        rows = (
            await session.scalars(select(TelegramUpdate).order_by(TelegramUpdate.update_id))
        ).all()
    assert [row.state for row in rows] == [UpdateState.FAILED, UpdateState.RECEIVED]
    assert error_text in rows[0].last_error


async def test_panel_network_failure_is_not_treated_as_unchanged():
    from tg_lottery.telegram.panel_edit import edit_panel

    error = TelegramNetworkError(method=EditMessageText(text="panel"), message="connection lost")
    message = AsyncMock()
    message.edit_text.side_effect = error
    with pytest.raises(TelegramNetworkError):
        await edit_panel(message, "panel")
