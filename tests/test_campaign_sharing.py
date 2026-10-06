import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tests.test_campaign_card_layout import card_payload
from tests.test_outbox_projection import _cipher, _worker
from tests.test_telegram_gateway_messages import FakeCardBot
from tests.v30_helpers import rules_for
from tg_lottery.application.campaign_sharing import CampaignShareRequest, request_campaign_share
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, Participant, TelegramOutbox, TicketLedger
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.gateway_requests import GatewayRequest, build_gateway_request
from tg_lottery.telegram.start_payloads import dispatch_start_payload

SHARE_USER = 202


async def seed_share_campaign(factory, *, state=CampaignState.RUNNING, cutoff_at=None):
    async with factory() as session, session.begin():
        _group, campaign, _participant = await seed_campaign(
            session, rules=rules_for("message"), state=state, cutoff_at=cutoff_at
        )
        campaign.presentation = card_payload()["presentation"]
        return campaign.id


async def request_share(factory, campaign_id, *, update_id=500):
    request = CampaignShareRequest(campaign_id, SHARE_USER, "lottery_test_bot")
    with bind_update_context(123, update_id):
        await request_campaign_share(partial(UnitOfWork, factory), request)


async def test_duplicate_share_updates_enqueue_one_card_without_registration(session_factory):
    cid = await seed_share_campaign(session_factory)
    await asyncio.gather(*(request_share(session_factory, cid) for _ in range(3)))
    async with session_factory() as session:
        rows = list(await session.scalars(select(TelegramOutbox)))
        assert len(rows) == 1
        row = rows[0]
        assert row.operation == "send_campaign_share_card"
        assert row.recipient_chat_id == SHARE_USER and row.state == OutboxState.PENDING
        assert row.payload["presentation"]["cover_file_id"] == "cover-file"
        assert "参与条件" in row.payload["rendered_card_text"]
        assert await session.scalar(select(func.count(Participant.id))) == 1
        assert not await session.scalar(
            select(Participant.id).where(Participant.user_id == SHARE_USER)
        )
        assert await session.scalar(select(func.count(TicketLedger.id))) == 0
        assert (await session.get(Campaign, cid)).state == CampaignState.RUNNING
    await request_share(session_factory, cid, update_id=501)
    async with session_factory() as session:
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 2


@pytest.mark.parametrize(
    "state", [CampaignState.PUBLISHING, CampaignState.CANCELLED, CampaignState.DRAWN]
)
async def test_closed_campaign_does_not_enqueue_active_share(session_factory, state):
    cid = await seed_share_campaign(session_factory, state=state)
    with pytest.raises(UserFacingError, match="未开放参与"):
        await request_share(session_factory, cid)
    async with session_factory() as session:
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 0


async def test_cutoff_rejects_new_share_request(session_factory):
    cid = await seed_share_campaign(
        session_factory, cutoff_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    with pytest.raises(UserFacingError, match="报名已截止"):
        await request_share(session_factory, cid)


async def test_queued_share_freezes_content_but_delivery_exposes_closed_state(session_factory):
    cid = await seed_share_campaign(session_factory)
    await request_share(session_factory, cid)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        campaign.name = "新名称不应覆盖已请求卡片"
        campaign.presentation = campaign.presentation | {"description": "新的说明"}
        campaign.state = CampaignState.CANCELLED
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
    request = await build_gateway_request(session_factory, row)
    assert "测试活动" in request.payload["rendered_card_text"]
    assert "已取消" in request.payload["rendered_card_text"]
    assert "新的说明" not in request.payload["rendered_card_text"]
    assert "点击链接参与抽奖" not in request.payload["rendered_card_text"]
    bot = FakeCardBot()
    await TelegramGateway(bot, object()).execute(request)
    button = bot.sent[0][2]["reply_markup"].inline_keyboard[0][0]
    assert "rules_" in button.url and button.callback_data is None
    assert not bot.pinned


async def test_queued_share_after_cutoff_does_not_claim_campaign_is_still_open(session_factory):
    cid = await seed_share_campaign(session_factory)
    await request_share(session_factory, cid)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        campaign.cutoff_at = datetime.now(UTC) - timedelta(minutes=1)
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
    request = await build_gateway_request(session_factory, row)
    assert "报名已截止" in request.payload["rendered_card_text"]
    assert "本期当前状态\uff1a进行中" not in request.payload["rendered_card_text"]
    assert request.payload["share_closed"] is True


@pytest.mark.parametrize("cover", [None, "cover-file"])
async def test_public_and_private_cards_use_same_native_caption_and_link(cover):
    payload = card_payload()
    payload["presentation"]["cover_file_id"] = cover
    bot = FakeCardBot()
    gateway = TelegramGateway(bot, object())
    await gateway.execute(GatewayRequest("send_campaign_card", -100123, payload))
    result = await gateway.execute(GatewayRequest("send_campaign_share_card", SHARE_USER, payload))
    group, private = bot.sent
    assert group[0] == private[0] == ("send_photo" if cover else "send_message")
    assert group[2] == private[2]
    assert group[2]["protect_content"] is False and group[2]["parse_mode"] == "HTML"
    if cover:
        assert group[1][1] == private[1][1] == cover
        assert group[2]["show_caption_above_media"] is False
    else:
        assert group[1][1] == private[1][1]
    assert result.remote_message_id is not None and len(bot.pinned) == 1


async def test_share_start_requests_card_for_sender_and_never_registers():
    campaign = SimpleNamespace(id="campaign-id")
    flow = SimpleNamespace(
        _queries=SimpleNamespace(campaign_by_reference=AsyncMock(return_value=campaign)),
        _participants=SimpleNamespace(request_share_card=AsyncMock()),
        _bot_username="lottery_test_bot",
        register=AsyncMock(),
    )
    message = SimpleNamespace(from_user=SimpleNamespace(id=SHARE_USER), answer=AsyncMock())
    assert await dispatch_start_payload(flow, message, "share_abcdefgh")
    flow._participants.request_share_card.assert_awaited_once_with(
        campaign.id, SHARE_USER, bot_username="lottery_test_bot"
    )
    flow.register.assert_not_awaited()
    assert "已提交" in message.answer.call_args.args[0]


async def test_share_delivery_failure_surfaces_without_pinning_or_remote_success():
    bot = FakeCardBot()
    bot.send_photo = AsyncMock(side_effect=RuntimeError("transport failure"))
    with pytest.raises(RuntimeError, match="transport failure"):
        await TelegramGateway(bot, object()).execute(
            GatewayRequest("send_campaign_share_card", SHARE_USER, card_payload())
        )
    assert not bot.pinned


@pytest.mark.parametrize("uncertain", [False, True])
async def test_share_outbox_tracks_delivery_without_projecting_campaign_publication(
    session_factory, uncertain
):
    cid = await seed_share_campaign(session_factory)
    await request_share(session_factory, cid)
    bot = FakeCardBot()
    if uncertain:
        bot.send_photo = AsyncMock(side_effect=TimeoutError("transport result unknown"))
    worker = _worker(session_factory, TelegramGateway(bot, object()), _cipher())
    assert await worker.process_once()
    assert not await worker.process_once()
    await request_share(session_factory, cid)
    async with session_factory() as session:
        rows = list(await session.scalars(select(TelegramOutbox)))
        assert len(rows) == 1
        row = rows[0]
        assert row.state == (OutboxState.UNKNOWN_HOLD if uncertain else OutboxState.CONFIRMED)
        assert (row.remote_message_id is None) is uncertain
        campaign = await session.get(Campaign, cid)
        assert campaign.published_message_id is None
        assert campaign.state == CampaignState.RUNNING
