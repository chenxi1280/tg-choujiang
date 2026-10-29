from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram.types import Chat, Message
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tests.test_config import _settings
from tg_lottery.application.featured_channel import (
    DIGEST_SEND,
    FEATURED_JOB,
    FEATURED_SEND,
    FeaturedChannelService,
)
from tg_lottery.application.records import add_outbox
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import Campaign, ScheduledJob, TelegramOutbox
from tg_lottery.ops.delivery_health import read_delivery_health
from tg_lottery.presentation.featured_channel import featured_content
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.gateway_requests import build_gateway_request
from tg_lottery.worker import _verify_featured_target

CHANNEL = -1004477119880
NOW = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)


def service(factory):
    return FeaturedChannelService(factory, chat_id=CHANNEL, bot_username="lottery_test_bot")


async def seed(factory, *, state=CampaignState.RUNNING, delivery=OutboxState.CONFIRMED):
    async with factory() as session, session.begin():
        group, campaign, _participant = await seed_campaign(
            session, state=state, cutoff_at=NOW + timedelta(days=1)
        )
        campaign.presentation = {"participation_groups": [{"title": "来源群"}]}
        row = add_outbox(
            session,
            operation="send_campaign_card",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=group.chat_id,
            business_key=f"publish:{campaign.id}",
            payload={},
        )
        row.state = delivery
        row.remote_message_id = 18 if delivery == OutboxState.CONFIRMED else None
        return campaign.id


async def scan(factory, monkeypatch, now=NOW, *, job=None):
    monkeypatch.setattr("tg_lottery.application.featured_channel.utcnow", lambda: now)
    await service(factory).run(job or SimpleNamespace(id=uuid4(), aggregate_id="system"))


async def rows(factory, operation):
    async with factory() as session:
        return tuple(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.operation == operation)
            )
        )


async def test_scan_replay_and_restart_do_not_duplicate_push_or_daily_digest(
    session_factory, monkeypatch
):
    await seed(session_factory)
    job = SimpleNamespace(id=uuid4(), aggregate_id="system")
    await scan(session_factory, monkeypatch, job=job)
    await scan(session_factory, monkeypatch, job=job)
    assert len(await rows(session_factory, FEATURED_SEND)) == 1
    assert len(await rows(session_factory, DIGEST_SEND)) == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count(ScheduledJob.id))) == 1


@pytest.mark.parametrize("delivery", [OutboxState.PENDING, OutboxState.UNKNOWN_HOLD])
async def test_unconfirmed_group_publication_is_not_promoted(
    session_factory, monkeypatch, delivery
):
    await seed(session_factory, delivery=delivery)
    await scan(session_factory, monkeypatch)
    assert not await rows(session_factory, FEATURED_SEND)
    assert not await rows(session_factory, DIGEST_SEND)


@pytest.mark.parametrize(
    "state", [CampaignState.PUBLISHING, CampaignState.CANCELLED, CampaignState.DRAWN]
)
async def test_non_open_activity_is_not_promoted(session_factory, monkeypatch, state):
    await seed(session_factory, state=state)
    await scan(session_factory, monkeypatch)
    assert not await rows(session_factory, FEATURED_SEND)


async def test_digest_uses_beijing_hour_and_each_day_once(session_factory, monkeypatch):
    await seed(session_factory)
    await scan(session_factory, monkeypatch, NOW - timedelta(minutes=1))
    assert len(await rows(session_factory, FEATURED_SEND)) == 1
    assert not await rows(session_factory, DIGEST_SEND)
    await scan(session_factory, monkeypatch)
    await scan(session_factory, monkeypatch, NOW + timedelta(hours=1))
    await scan(session_factory, monkeypatch, NOW + timedelta(days=1) - timedelta(minutes=1))
    assert len(await rows(session_factory, DIGEST_SEND)) == 1
    # Exactly at cutoff the activity must be omitted, even if the cutoff job is delayed.
    await scan(session_factory, monkeypatch, NOW + timedelta(days=1))
    assert len(await rows(session_factory, DIGEST_SEND)) == 1


async def test_unknown_channel_delivery_is_retained_without_new_push(session_factory, monkeypatch):
    await seed(session_factory)
    await scan(session_factory, monkeypatch)
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == FEATURED_SEND)
        )
        row.state = OutboxState.UNKNOWN_HOLD
        row.last_error = "ambiguous network result"
    await scan(session_factory, monkeypatch)
    result = await rows(session_factory, FEATURED_SEND)
    assert len(result) == 1 and result[0].state == OutboxState.UNKNOWN_HOLD
    assert result[0].last_error == "ambiguous network result"


async def test_delivery_rechecks_state_and_uses_same_campaign_link(session_factory, monkeypatch):
    cid = await seed(session_factory)
    await scan(session_factory, monkeypatch)
    row = (await rows(session_factory, FEATURED_SEND))[0]
    monkeypatch.setattr("tg_lottery.telegram.gateway_featured_projection.utcnow", lambda: NOW)
    request = await build_gateway_request(session_factory, row)
    bot = SimpleNamespace(
        send_message=AsyncMock(
            return_value=Message(message_id=51, date=NOW, chat=Chat(id=CHANNEL, type="channel"))
        )
    )
    await TelegramGateway(bot, object()).execute(request)
    sent = bot.send_message.call_args.kwargs
    button = sent["reply_markup"].inline_keyboard[0][0]
    assert "start=join_" in button.url and button.url in sent["text"]
    assert sent["chat_id"] == CHANNEL and sent["disable_notification"] is False
    async with session_factory() as session, session.begin():
        (await session.get(Campaign, cid)).state = CampaignState.CANCELLED
    closed = await build_gateway_request(session_factory, row)
    text, buttons = featured_content(closed.payload, digest=False)
    assert "已取消" in text and "点击参与" not in text
    assert "start=rules_" in buttons[0]["url"]


async def test_confirmed_channel_post_tracks_cancellation_and_resume(session_factory, monkeypatch):
    cid = await seed(session_factory)
    await scan(session_factory, monkeypatch)
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == FEATURED_SEND)
        )
        row.state, row.remote_message_id = OutboxState.CONFIRMED, 71
        (await session.get(Campaign, cid)).state = CampaignState.PAUSED
    await scan(session_factory, monkeypatch)
    await scan(session_factory, monkeypatch)
    edits = await rows(session_factory, "edit_featured_campaign")
    assert len(edits) == 1 and edits[0].payload["message_id"] == 71
    async with session_factory() as session, session.begin():
        (await session.get(Campaign, cid)).state = CampaignState.RUNNING
    await scan(session_factory, monkeypatch)
    assert len(await rows(session_factory, "edit_featured_campaign")) == 2


async def test_startup_seeds_one_job_chain(session_factory):
    await service(session_factory).ensure_job(NOW)
    await service(session_factory).ensure_job(NOW + timedelta(seconds=1))
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(ScheduledJob.id)).where(ScheduledJob.job_type == FEATURED_JOB)
            )
            == 1
        )


def test_push_config_requires_stable_channel_identity():
    with pytest.raises(ValueError, match="negative channel id"):
        _settings(featured_push_enabled=True)
    assert (
        _settings(featured_push_enabled=True, featured_push_chat_id=CHANNEL).featured_digest_hour
        == 10
    )


async def test_startup_checks_channel_and_publish_edit_permissions():
    settings = _settings(featured_push_enabled=True, featured_push_chat_id=CHANNEL)
    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(id=CHANNEL, type="channel")),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(
                status="administrator", can_post_messages=False, can_edit_messages=True
            )
        ),
    )
    with pytest.raises(ValueError, match="post and edit"):
        await _verify_featured_target(SimpleNamespace(settings=settings, bot=bot))


async def test_daily_digest_paginates_and_repeats_on_next_beijing_day(session_factory, monkeypatch):
    cid = await seed(session_factory)
    async with session_factory() as session, session.begin():
        first = await session.get(Campaign, cid)
        first.cutoff_at = NOW + timedelta(days=3)
        for index in range(5):
            campaign = Campaign(
                group_id=first.group_id,
                name=f"活动{index}",
                state=CampaignState.RUNNING,
                timezone=first.timezone,
                preheat_at=first.preheat_at,
                cutoff_at=first.cutoff_at,
                draw_at=first.draw_at,
                rules=first.rules,
                config_revision=1,
                created_by=101,
                presentation={},
            )
            session.add(campaign)
            await session.flush()
            row = add_outbox(
                session,
                operation="send_campaign_card",
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
                recipient_chat_id=-100123,
                business_key=f"publish:{campaign.id}",
                payload={},
            )
            row.state, row.remote_message_id = OutboxState.CONFIRMED, index + 30
    await scan(session_factory, monkeypatch)
    assert len(await rows(session_factory, FEATURED_SEND)) == 6
    pages = await rows(session_factory, DIGEST_SEND)
    assert sorted(len(p.payload["items"]) for p in pages) == [2, 4]
    await scan(session_factory, monkeypatch, NOW + timedelta(days=1))
    assert len(await rows(session_factory, DIGEST_SEND)) == 4


async def test_permission_failure_is_recordable_without_creating_pushes(session_factory):
    verifier = AsyncMock(side_effect=ValueError("channel permission missing"))
    promoter = FeaturedChannelService(
        session_factory, chat_id=CHANNEL, bot_username="lottery_test_bot", verify_target=verifier
    )
    with pytest.raises(ValueError, match="permission missing"):
        await promoter.run(SimpleNamespace(id=uuid4(), aggregate_id="system"))
    assert not await rows(session_factory, FEATURED_SEND)


def test_featured_text_escapes_names_and_bounds_long_digest():
    item = {
        "name": "<b>&" * 16,
        "prizes": "🎁" * 120,
        "groups": "😀" * 90,
        "campaign_ref": "AbCdEfGhIjKlMnOpQrSt-_",
        "cutoff_at": NOW.isoformat(),
        "draw_at": NOW.isoformat(),
    }
    payload = {"date": "2026-10-29", "bot_username": "lottery_test_bot", "items": [item] * 4}
    text, buttons = featured_content(payload, digest=True)
    assert "&lt;b&gt;&amp;" in text
    assert len(text.encode("utf-16-le")) // 2 < 4096
    assert len(buttons) == 4


@pytest.mark.parametrize("operation", [FEATURED_SEND, DIGEST_SEND, "edit_featured_campaign"])
async def test_failed_featured_delivery_is_visible_in_health(session_factory, operation):
    async with session_factory() as session, session.begin():
        row = add_outbox(
            session,
            operation=operation,
            aggregate_type="system",
            aggregate_id="system",
            recipient_chat_id=CHANNEL,
            business_key=f"failed:{operation}",
            payload={},
        )
        row.state = OutboxState.PRE_ACCEPT_FAILED
        row.last_error = "channel write rejected"
    health = await read_delivery_health(session_factory)
    assert health.failed_notices == 1
