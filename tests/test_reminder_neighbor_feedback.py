from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from tests.reminder_neighbor_helpers import (
    CHAT_ID,
    REFERENCE,
    USER_ID,
    campaign,
    flow_for,
    handler_for,
    queries_for,
)
from tests.test_v32_member_navigation import _query
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError


@pytest.mark.parametrize(
    "route",
    [
        f"m:bo:{REFERENCE}",
        f"member:bonus_activity:{REFERENCE}:0",
        f"member:bonus_invites:{REFERENCE}:0",
    ],
)
async def test_actual_navigation_unregistered_group_error_queues_temporary_reminder(
    monkeypatch, route
):
    popup = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", popup)
    flow = flow_for(queries_for(campaign(), registered=False))
    query = _query(CHAT_ID, kind="supergroup").model_copy(update={"data": route})
    await handler_for(flow)._callback(query)
    values = flow._participants.queue_temporary_reminder.await_args.kwargs
    assert values["recipient_chat_id"] == CHAT_ID
    assert values["campaign_ref"] == REFERENCE
    assert values["business_key"].endswith(":" + query.id)
    popup.assert_awaited_once()
    flow._bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "route,bonuses", [(f"m:bo:{REFERENCE}", ()), (f"m:ad:{REFERENCE}:-199999:0", ("activity",))]
)
async def test_no_matching_group_rule_uses_temporary_feedback(monkeypatch, route, bonuses):
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    flow = flow_for(queries_for(campaign(bonuses=bonuses)))
    query = _query(CHAT_ID, kind="supergroup").model_copy(update={"data": route})
    await handler_for(flow)._callback(query)
    flow._participants.queue_temporary_reminder.assert_awaited_once()
    flow._bot.send_message.assert_not_awaited()


async def test_private_navigation_failure_does_not_queue_public_reminder(monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    flow = flow_for(queries_for(campaign(), registered=False))
    query = _query().model_copy(update={"data": f"m:bo:{REFERENCE}"})
    await handler_for(flow)._callback(query)
    assert "操作未完成" in answer.await_args.args[0]
    flow._participants.queue_temporary_reminder.assert_not_awaited()


async def test_unrelated_error_is_not_turned_into_temporary_reminder(monkeypatch):
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    flow = flow_for(queries_for(campaign()))
    query = _query(CHAT_ID, kind="supergroup").model_copy(update={"data": f"m:bo:{REFERENCE}"})
    await handler_for(flow)._callback_error(query, UserFacingError("没有操作权限"))
    flow._participants.queue_temporary_reminder.assert_not_awaited()


async def test_malformed_old_navigation_keeps_error_feedback_without_queueing(monkeypatch):
    popup = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", popup)
    flow = flow_for(queries_for(campaign(), registered=False))
    query = _query(CHAT_ID, kind="supergroup").model_copy(update={"data": f"m:ad:{REFERENCE}"})
    await handler_for(flow)._callback(query)
    popup.assert_awaited_once()
    flow._participants.queue_temporary_reminder.assert_not_awaited()


@pytest.mark.parametrize("case", ["success", "blocked", "unknown", "rejected"])
async def test_qualification_private_delivery_reports_actual_result_once(monkeypatch, caplog, case):
    popup, public_send = AsyncMock(), AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", popup)
    monkeypatch.setattr(Message, "answer", public_send)
    flow = flow_for(queries_for(campaign()))
    flow._qualification_reply = AsyncMock(
        return_value=("私人条件详情", InlineKeyboardMarkup(inline_keyboard=[]))
    )
    method = SendMessage(chat_id=USER_ID, text="私人条件详情")
    if case == "blocked":
        flow._bot.send_message.side_effect = TelegramForbiddenError(
            method=method, message="blocked"
        )
    elif case == "unknown":
        flow._bot.send_message.side_effect = TelegramNetworkError(method=method, message="timeout")
    elif case == "rejected":
        flow._bot.send_message.side_effect = TelegramBadRequest(
            method=method, message="chat not found"
        )
    query = _query(CHAT_ID, kind="supergroup")
    await flow._send_qualification_failure(
        query.message,
        campaign=campaign(),
        state=QualificationState.INELIGIBLE,
        observations=(),
        user_id=USER_ID,
        page=1,
        callback=query,
    )
    popup.assert_awaited_once()
    text = popup.await_args.args[0]
    assert "私人条件详情" not in text
    assert flow._bot.send_message.await_args.args[0] == USER_ID
    public_send.assert_not_awaited()
    if case == "success":
        assert "已发送到本人私聊" in text
    else:
        label = {"blocked": "私聊不可达", "unknown": "结果未确认", "rejected": "发送失败"}[case]
        assert label in text
        assert "Qualification detail private delivery failed" in caplog.text


@pytest.mark.parametrize("registered", [True, False])
async def test_participation_card_opens_clickers_private_detail_without_start(
    monkeypatch, registered
):
    from tg_lottery.telegram.gateway import _campaign_keyboard

    record = campaign()
    record.config_revision = 1
    queries = queries_for(record, registered=registered)
    queries.prizes = AsyncMock(return_value=())
    queries.operational_issues = AsyncMock(return_value=())
    flow = flow_for(queries)
    delivered = []

    async def receive(message, text, **options):
        delivered.append((message.chat.id, text, options))

    monkeypatch.setattr(Message, "answer", receive)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    route = (
        _campaign_keyboard(
            {"campaign_ref": REFERENCE, "bot_username": "test_bot", "rules": record.rules}
        )
        .inline_keyboard[0][0]
        .callback_data
    )
    query = _query(CHAT_ID, kind="supergroup").model_copy(update={"data": route})
    handler = handler_for(flow)
    await handler._callback(query)
    assert len(delivered) == 1
    assert delivered[0][0] == USER_ID
    queries.participant_campaign_detail.assert_any_await(USER_ID, REFERENCE)
    requests = handler._navigation._tokens.create_many.await_args.kwargs["requests"]
    if registered:
        assert "本人活动" in delivered[0][1]
    else:
        assert "提醒回归" in delivered[0][1]
        routes = {r.payload["route"] for r in requests}
        assert f"m:conditions:{REFERENCE}:0" in routes
        assert "登记参与" in str(delivered[0][2]["reply_markup"])
    flow._participants.queue_temporary_reminder.assert_not_awaited()
