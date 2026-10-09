from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import select

from tests.test_inbox_ordering import _processor, _stored_update
from tests.test_panel_edit_recovery import PanelBot
from tests.test_update_handler import _callback, _handler, _RecordingCallbackQuery
from tg_lottery.domain.enums import UpdateState
from tg_lottery.infrastructure.db import TelegramUpdate
from tg_lottery.telegram import member_callbacks, panel_edit
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.gateway_requests import GatewayRequest

UNCHANGED = "Bad Request: message is not modified: content and markup are exactly the same"


@pytest.mark.parametrize(
    "reason",
    [
        "message to edit not found",
        "message can't be edited",
        "there is no text in the message to edit",
    ],
)
async def test_unavailable_panel_provides_recovery_and_next_start_is_processed(
    session_factory, reason
):
    row = _stored_update(100)
    row.update_type = "callback_query"
    row.payload = {
        "update_id": 100,
        "callback_query": _callback("secure:expired-panel").model_dump(mode="json"),
    }
    async with session_factory() as session, session.begin():
        session.add_all([row, _stored_update(101)])
    error = TelegramBadRequest(
        EditMessageText(chat_id=201, message_id=10, text="panel"), f"Bad Request: {reason}"
    )
    bot = PanelBot(error)
    handler = _handler()

    async def edit(query, data):
        await panel_edit.edit_panel(query.message, "panel")

    handler._dispatch_callback = edit
    handler._personal_flow.show_index = AsyncMock()
    processor = _processor(handler, session_factory, bot=bot)
    assert await processor.process_once() and await processor.process_once()
    async with session_factory() as session:
        states = (await session.scalars(select(TelegramUpdate.state))).all()
    assert states == [UpdateState.PROCESSED] * 2
    recovery = next(method for method in bot.methods if isinstance(method, SendMessage))
    assert "/menu" in recovery.text and "已保存的数据保留" in recovery.text
    assert recovery.text.startswith("页面显示未完成:")
    handler._personal_flow.show_index.assert_awaited_once()


@pytest.mark.parametrize(
    "operation", ["edit_campaign_card_state", "edit_campaign_card_cancelled", "edit_campaign_rules"]
)
@pytest.mark.parametrize("cover", [False, True])
async def test_gateway_confirms_only_explicit_unchanged_edit_to_same_message(operation, cover):
    error = TelegramBadRequest(EditMessageText(text="panel"), UNCHANGED)
    bot = AsyncMock()
    bot.edit_message_text.side_effect = error
    bot.edit_message_caption.side_effect = error
    gateway = TelegramGateway(bot, object())
    payload = {
        "message_id": 77,
        "campaign_name": "test",
        "card_state": "drawn",
        "text": "rules",
        "has_cover": cover,
    }
    result = await gateway.execute(GatewayRequest(operation, -1001, payload))
    assert result.remote_message_id == 77
    assert result.remote_result == {"message_id": 77, "unchanged": True}
    bot.send_message.assert_not_awaited()
    bot.send_photo.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [
        TelegramBadRequest(EditMessageText(text="panel"), "Bad Request: message to edit not found"),
        TelegramNetworkError(EditMessageText(text="panel"), "connection lost"),
    ],
)
async def test_gateway_missing_or_uncertain_edit_is_not_confirmed(failure):
    bot = AsyncMock()
    bot.edit_message_text.side_effect = failure
    gateway = TelegramGateway(bot, object())
    with pytest.raises(type(failure)):
        await gateway.execute(
            GatewayRequest(
                "edit_campaign_card_state",
                -1001,
                {"message_id": 77, "campaign_name": "test", "card_state": "drawn"},
            )
        )
    bot.send_message.assert_not_awaited()


async def test_unknown_and_inaccessible_buttons_have_visible_feedback():
    handler = _handler()
    handler._member_flow = AsyncMock()
    for message in (_callback("unknown:old").message, None):
        query = _RecordingCallbackQuery("unknown:old", message)
        await handler._callback(query)
        assert (
            ("此按钮已失效, 请发送 /menu 重新打开当前页面。", True) in query.answers
            if message
            else ("原页面已失效, 请发送 /menu 重新打开。", True) in query.answers
        )


async def test_result_network_rejection_is_logged_without_business_replay(caplog):
    query = _RecordingCallbackQuery("member:noop", _callback("member:noop").message)
    query.answer = AsyncMock(
        side_effect=TelegramNetworkError(EditMessageText(text="unused"), "connection reset")
    )
    await member_callbacks.safe_answer(query, "已提交", alert=True)
    assert any(
        getattr(r, "operation", None) == "callback_result" and r.completed is False
        for r in caplog.records
    )
