from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_update_handler import _group_message, _handler


@pytest.mark.parametrize(
    "text", ["/getid", "/region@tgccjbot", "/new", "/start", "/progress", "/lottery"]
)
async def test_english_group_commands_do_not_reply_or_count(text):
    handler = _handler()
    handler._bot_username = "tgccjbot"
    handler._activity = SimpleNamespace(record_message=AsyncMock())
    message = _group_message(text=text)
    await handler._message(message)
    message.answer.assert_not_awaited()
    assert handler._stats.recorded == []
    handler._activity.record_message.assert_not_awaited()


@pytest.mark.parametrize("text", ["progress", "lottery", "ordinary English conversation"])
async def test_english_group_text_still_records_activity(text):
    handler = _handler()
    handler._activity = SimpleNamespace(record_message=AsyncMock())
    message = _group_message(text=text)
    await handler._message(message)
    message.answer.assert_not_awaited()
    assert [observation.text for observation in handler._stats.recorded] == [text]
    handler._activity.record_message.assert_awaited_once_with(handler._stats.recorded[0])
