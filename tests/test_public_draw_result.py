from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.test_multi_prize_draw import _seed_multi_prize_case
from tests.test_telegram_gateway_messages import FakeCardBot
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, Participant, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.gateway_requests import GatewayRequest
from tg_lottery.telegram.public_draw_result import show_public_result
from tg_lottery.telegram.start_payloads import dispatch_start_payload


async def test_result_link_queries_saved_draw_without_registering_and_survives_archive(
    session_factory,
):
    campaign_id = await _seed_multi_prize_case(session_factory, datetime.now(UTC))
    factory = partial(UnitOfWork, session_factory)
    await SettlementService(factory).settle(campaign_id, datetime.now(UTC))
    await DrawingService(factory).run(campaign_id, actor_user_id=101)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        reference = campaign.public_ref
        campaign.state = CampaignState.ARCHIVED
    queries = QueryService(session_factory)
    flow = SimpleNamespace(_queries=queries, _bot_username="test_bot", register=AsyncMock())
    message = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=999))
    assert await dispatch_start_payload(flow, message, "history_" + reference)
    text = message.answer.call_args.args[0]
    assert "CJ-" not in text and "结果编号" not in text
    assert ">查看开奖结果</a>" in text
    assert f"https://t.me/test_bot?start=history_{reference}" in text
    assert "实际开奖时间" in text and "北京时间" in text
    assert "成员一" in text and "未分配名额" in text and "二等奖: 1 人" in text
    assert "snapshot_hash" not in text and "random_input" not in text
    flow.register.assert_not_awaited()
    async with session_factory() as session:
        assert await session.scalar(select(Participant).where(Participant.user_id == 999)) is None
        results = (
            await session.scalars(
                select(TelegramOutbox).where(
                    TelegramOutbox.operation.in_(["send_draw_result", "send_winner_notice"])
                )
            )
        ).all()
    assert all(row.payload["campaign_ref"] == reference for row in results)
    bot = FakeCardBot()
    gateway = TelegramGateway(bot, object(), bot_username="test_bot")
    for row in results:
        await gateway.execute(GatewayRequest(row.operation, row.recipient_chat_id, row.payload))
    assert all(f"?start=history_{reference}" in sent[1][1] for sent in bot.sent)


@pytest.mark.parametrize("kind,expected", [("cancelled", "已取消"), ("unconfirmed", "尚无完成")])
async def test_missing_completed_draw_cannot_be_shown_as_a_result(kind, expected):
    flow = SimpleNamespace(
        _queries=SimpleNamespace(
            campaign_by_reference=AsyncMock(return_value=SimpleNamespace(id=1)),
            campaign_outcome=AsyncMock(return_value=SimpleNamespace(kind=kind)),
            winners=AsyncMock(),
        )
    )
    message = SimpleNamespace(answer=AsyncMock())
    await show_public_result(flow, message, "AbCdEfGhIjKlMnOpQrSt-_")
    assert expected in message.answer.call_args.args[0]
    flow._queries.winners.assert_not_awaited()


async def test_invalid_number_is_explicit_and_never_queries_winners():
    flow = SimpleNamespace(_queries=AsyncMock())
    with pytest.raises(UserFacingError, match="编号无效"):
        await show_public_result(flow, AsyncMock(), "invalid")
    flow._queries.campaign_by_reference.assert_not_awaited()


async def test_unregistered_activity_opens_compact_detail_with_explicit_registration(
    session_factory, monkeypatch
):
    from aiogram.types import Chat, Message, User

    from tests.helpers import seed_campaign
    from tests.test_v32_member_navigation import _navigation

    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        reference = campaign.public_ref
    navigation, _, _ = _navigation()
    navigation._queries = QueryService(session_factory)
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    message = Message(
        message_id=8,
        date=datetime.now(UTC),
        chat=Chat(id=202, type="private"),
        from_user=User(id=202, is_bot=False, first_name="Viewer"),
    )
    await navigation.open_message(message, reference)
    text = answer.call_args.args[0]
    assert "活动详情" in text and "尚未登记" in text
    assert "完整统计与加成规则" not in text
    requests = navigation._tokens.create_many.call_args.kwargs["requests"]
    keyboard = answer.call_args.kwargs["reply_markup"]
    assert any(
        button.callback_data == f"member:join:{reference}"
        for row in keyboard.inline_keyboard
        for button in row
    )
    assert any(request.payload.get("route", "").startswith("m:conditions:") for request in requests)
    async with session_factory() as session:
        assert await session.scalar(select(Participant).where(Participant.user_id == 202)) is None
