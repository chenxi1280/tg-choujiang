from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tests.test_campaign_card_layout import card_payload
from tests.test_notice_deletion import _deletion_worker
from tg_lottery.application.channel_shortcuts import queue_channel_shortcut
from tg_lottery.application.participant_records import load_participated_campaign_page
from tg_lottery.application.public_participation import public_participation
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.application.start_notices import queue_start_notices
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox
from tg_lottery.telegram.channel_shortcuts import SendPersonalMessage, _send_personal
from tg_lottery.telegram.gateway_start import deliver_start


async def _original_card(session, campaign, *, channel=False):
    campaign.presentation = card_payload()["presentation"] | {"ux_policy_version": "r14"}
    if channel:
        campaign.presentation |= {"main_chat": {"id": -100123, "type": "channel"}}
    row = add_outbox(
        session,
        operation="send_campaign_card",
        aggregate_type="campaign",
        aggregate_id=str(campaign.id),
        recipient_chat_id=-100123,
        business_key="original-card",
        payload=card_payload()
        | {"campaign_id": str(campaign.id), "campaign_ref": campaign.public_ref},
    )
    row.state = OutboxState.CONFIRMED
    row.remote_message_id = 55
    await session.flush()
    return row


async def test_scheduled_start_commits_state_and_one_complete_notice(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=CampaignState.SCHEDULED)
        campaign.preheat_at = utcnow() - timedelta(seconds=10)
        await _original_card(session, campaign)
        campaign_id = campaign.id
    handlers = _deletion_worker(session_factory)._handlers
    job = SimpleNamespace(aggregate_id=str(campaign_id))
    await handlers._campaign_start(job)
    await handlers._campaign_start(job)
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign.state == CampaignState.RUNNING
        assert campaign.presentation["start_notice_intent"]
        rows = list(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.operation.like("send_start_%"))
            )
        )
        assert len(rows) == 1
        text = rows[0].payload["rendered_card_text"]
        assert text.startswith("抽奖活动已经开始\n@全体成员\n")
        assert "抽奖说明" in text and "参与情况" in text
        assert "delete_after_seconds" not in rows[0].payload


async def test_existing_running_campaign_does_not_backfill_start(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        await _original_card(session, campaign)
        campaign_id = campaign.id
    await _deletion_worker(session_factory)._handlers._campaign_start(
        SimpleNamespace(aggregate_id=str(campaign_id))
    )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(TelegramOutbox)) == 1


async def test_started_before_publication_receipt_recovers_target_without_duplicates(
    session_factory,
):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        await queue_start_notices(session, campaign)
        await session.flush()
        await _original_card(session, campaign)
        await queue_start_notices(session, campaign)
        await session.flush()
        await queue_start_notices(session, campaign)
        assert await session.scalar(select(func.count()).select_from(TelegramOutbox)) == 2


async def test_channel_helper_is_single_and_never_posts_to_other_required_channels(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        source = await _original_card(session, campaign, channel=True)
        campaign.presentation |= {"required_chats": [{"id": -100999, "type": "channel"}]}
        await queue_channel_shortcut(session, source)
        await session.flush()
        await queue_channel_shortcut(session, source)
        helpers = list(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.operation == "send_channel_shortcut")
            )
        )
        assert len(helpers) == 1 and helpers[0].recipient_chat_id == -100123


async def test_pending_intent_is_separate_from_registered_count_and_list(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        participant.registration_intent_at = utcnow()
        await session.flush()
        text = await public_participation(session, campaign.id)
        assert "0人报名" in text
    active = await load_participated_campaign_page(session_factory, 201, category="active", page=0)
    pending = await load_participated_campaign_page(
        session_factory, 201, category="incomplete", page=0
    )
    assert not active.rows
    assert len(pending.rows) == 1 and not pending.rows[0].registered


async def test_start_card_has_no_new_pin_operation(monkeypatch):
    bot = SimpleNamespace(pin_chat_message=AsyncMock())
    from aiogram.types import Chat, Message

    message = Message(message_id=8, date=utcnow(), chat=Chat(id=-100123, type="supergroup"))
    deliver = AsyncMock(return_value=message)
    monkeypatch.setattr("tg_lottery.telegram.gateway.deliver_campaign_card", deliver)
    await deliver_start(
        bot, SimpleNamespace(operation="send_start_card", recipient_chat_id=-100123, payload={})
    )
    bot.pin_chat_message.assert_not_called()
    deliver.assert_awaited_once()


async def test_personal_overlay_always_targets_the_clicker_and_never_edits_shared_message():
    bot = AsyncMock()
    query = SimpleNamespace(
        id="cb",
        from_user=SimpleNamespace(id=201),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100123), ephemeral_message_id=None),
    )
    await _send_personal(bot, query, text="本人条件", keyboard=None)
    request = bot.await_args.args[0]
    assert isinstance(request, SendPersonalMessage)
    assert request.model_dump()["ephemeral_message_parameters"] == {
        "receiver_user_id": 201,
        "callback_query_id": "cb",
    }
    bot.edit_message_text.assert_not_called()
