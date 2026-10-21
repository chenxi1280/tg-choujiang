from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerInlineQuery
from aiogram.types import Update
from sqlalchemy import func, select

from tests.test_campaign_sharing import request_share, seed_share_campaign
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState, UpdateState
from tg_lottery.infrastructure.db import (
    Campaign,
    Participant,
    TelegramOutbox,
    TelegramUpdate,
    TicketLedger,
)
from tg_lottery.telegram.gateway import _campaign_keyboard
from tg_lottery.telegram.inbox import InboxProcessor
from tg_lottery.telegram.inline_sharing import answer_inline_share
from tg_lottery.telegram.response_rejection import rejected_source_response
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


def inline_update(reference, *, query_id="inline-1"):
    return Update.model_validate(
        {
            "update_id": 700,
            "inline_query": {
                "id": query_id,
                "from": {"id": 202, "is_bot": False, "first_name": "分享者"},
                "query": "share_" + reference,
                "offset": "",
                "chat_type": "supergroup",
            },
        }
    )


@pytest.mark.parametrize("cover", [None, "saved-cover-file"])
async def test_group_share_dispatch_returns_complete_card_without_registration(
    session_factory, cover
):
    cid = await seed_share_campaign(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        campaign.presentation = campaign.presentation | {"cover_file_id": cover}
        reference = campaign.public_ref
    handler = object.__new__(LotteryUpdateHandler)
    handler._bot = SimpleNamespace(answer_inline_query=AsyncMock())
    handler._bot_username = "lottery_test_bot"
    handler._queries = QueryService(session_factory)
    await handler.handle(inline_update(reference))
    answer = handler._bot.answer_inline_query.await_args
    assert answer.args == ("inline-1",)
    assert answer.kwargs["cache_time"] == 0
    result = answer.kwargs["results"][0]
    text = result.caption if cover else result.input_message_content.message_text
    assert "参与条件" in text and "奖品内容" in text
    buttons = [button for row in result.reply_markup.inline_keyboard for button in row]
    link = f"https://t.me/lottery_test_bot?start=join_{reference}"
    assert link in text and buttons[0].url == link
    assert all(button.callback_data is None for button in buttons)
    chooser = buttons[-1].switch_inline_query_chosen_chat
    assert chooser.allow_group_chats and not chooser.allow_user_chats
    assert not chooser.allow_channel_chats and not chooser.allow_bot_chats
    assert chooser.query == "share_" + reference
    if cover:
        assert result.photo_file_id == cover
    async with session_factory() as session:
        assert await session.scalar(select(func.count(Participant.id))) == 1
        assert await session.scalar(select(func.count(TicketLedger.id))) == 0
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 0


async def test_private_share_card_exposes_group_chooser_and_portable_buttons(session_factory):
    cid = await seed_share_campaign(session_factory)
    await request_share(session_factory, cid)
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
        buttons = [b for line in _campaign_keyboard(row.payload).inline_keyboard for b in line]
        assert any(b.switch_inline_query_chosen_chat for b in buttons)
        assert all(b.callback_data is None for b in buttons)


@pytest.mark.parametrize(
    "state", [CampaignState.PUBLISHING, CampaignState.CANCELLED, CampaignState.DRAWN]
)
async def test_unpublished_or_closed_campaign_is_not_shareable(session_factory, state):
    cid = await seed_share_campaign(session_factory, state=state)
    async with session_factory() as session:
        campaign = await session.get(Campaign, cid)
    bot = SimpleNamespace(answer_inline_query=AsyncMock())
    await answer_inline_share(
        bot,
        inline_update(campaign.public_ref).inline_query,
        queries=QueryService(session_factory),
        bot_username="lottery_test_bot",
    )
    assert bot.answer_inline_query.await_args.kwargs["results"] == []


async def test_expired_campaign_and_malformed_reference_do_not_leak_or_share(session_factory):
    cid = await seed_share_campaign(
        session_factory, cutoff_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    queries = QueryService(session_factory)
    async with session_factory() as session:
        reference = (await session.get(Campaign, cid)).public_ref
    assert await queries.campaign_share_payload(reference, bot_username="test_bot") is None
    fake_queries = SimpleNamespace(campaign_share_payload=AsyncMock())
    bot = SimpleNamespace(answer_inline_query=AsyncMock())
    await answer_inline_share(
        bot,
        inline_update("bad!").inline_query,
        queries=fake_queries,
        bot_username="test_bot",
    )
    fake_queries.campaign_share_payload.assert_not_awaited()
    assert bot.answer_inline_query.await_args.kwargs["results"] == []


async def test_expired_inline_response_is_recorded_and_does_not_block_the_inbox(session_factory):
    update = inline_update("x" * 22)
    error = TelegramBadRequest(
        method=AnswerInlineQuery(inline_query_id="inline-1", results=[]),
        message="Bad Request: query is too old and response timeout expired or query ID is invalid",
    )
    assert rejected_source_response(update, error)
    wrong_query = inline_update("x" * 22, query_id="other")
    assert not rejected_source_response(wrong_query, error)
    async with session_factory() as session, session.begin():
        session.add(
            TelegramUpdate(
                bot_id=123,
                update_id=700,
                update_type="inline_query",
                event_at=datetime.now(UTC),
                payload=update.model_dump(mode="json"),
                payload_hash="a" * 64,
                state=UpdateState.RECEIVED,
                attempts=0,
            )
        )
    processor = InboxProcessor(
        bot=object(),
        bot_id=123,
        session_factory=session_factory,
        handler=SimpleNamespace(handle=AsyncMock(side_effect=error)),
    )
    assert await processor.process_once()
    async with session_factory() as session:
        row = await session.scalar(select(TelegramUpdate))
        assert row.state == UpdateState.PROCESSED and "UI response rejected" in row.last_error
