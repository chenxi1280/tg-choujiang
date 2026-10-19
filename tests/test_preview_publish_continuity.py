"""Preview navigation must preserve confirmation through the publication Outbox."""

from base64 import urlsafe_b64encode
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.test_create_entry_incident import PANEL_ID, USER_ID, _query
from tests.test_template_schedule import _complete_draft
from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import OutboxState
from tg_lottery.domain.errors import RecoverableDraftCallback
from tg_lottery.infrastructure.db import Campaign, Group, GroupAdmin, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram import command_input
from tg_lottery.telegram.creator_publication import CreatorPublisher
from tg_lottery.telegram.creator_suspension import suspend_draft
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.outbox import OutboxWorker
from tg_lottery.telegram.private_update_navigation import private_message, should_suspend
from tg_lottery.telegram.start_payloads import unpublished_start

PREVIEWS = ["join_unpublished", "rules_unpublished", "condition_unpublished_0"]


@pytest.mark.parametrize("payload", PREVIEWS)
@pytest.mark.parametrize("suffix", ["", "@lottery_test_bot"])
def test_preview_start_preserves_creation_context(payload, suffix):
    command = command_input.parse_bot_command(f"/start{suffix} {payload}")
    assert not should_suspend(command, bot_username="lottery_test_bot")


@pytest.mark.parametrize("text", ["/start", "/menu", "/help", "/start join_real"])
def test_other_navigation_still_suspends(text):
    assert should_suspend(command_input.parse_bot_command(text), bot_username="lottery_test_bot")


async def _preview(sessions, payload):
    async def suspend(user_id):
        await suspend_draft(sessions, user_id)

    async def navigate(message, text):
        return await unpublished_start(message, text.split(maxsplit=1)[1])

    handler = SimpleNamespace(
        _bot_username="lottery_test_bot",
        _member_flow=SimpleNamespace(),
        _admin_flow=SimpleNamespace(suspend_draft=suspend),
        _private_navigation=navigate,
        _send_operation_error=AsyncMock(),
    )
    message = SimpleNamespace(
        text=f"/start {payload}",
        from_user=SimpleNamespace(id=USER_ID, is_bot=False),
        answer=AsyncMock(),
    )
    await private_message(handler, message)
    assert "未发布" in message.answer.await_args.args[0]
    handler._send_operation_error.assert_not_awaited()


async def _confirmation(session_factory):
    factory = partial(UnitOfWork, session_factory)
    sessions, tokens = AdminSessionService(factory), CallbackTokenService(factory)
    data = _complete_draft() | {
        "draft_id": "preview-confirm-regression",
        "draft_revision": 1,
        "panel_message_id": PANEL_ID,
        "platform_policy": None,
    }
    await sessions.set(USER_ID, "draft:publish_confirm", data)
    (token,) = await tokens.create_many(
        owner_user_id=USER_ID,
        chat_id=USER_ID,
        requests=(CallbackRequest("draft_publish_confirm", payload=data),),
    )
    return sessions, tokens, token, data


@pytest.mark.parametrize("scenario", [(p, c) for p in PREVIEWS for c in (None, "test-cover")])
async def test_preview_then_confirmation_delivers_group_card(
    session_factory, monkeypatch, scenario
):
    payload, cover = scenario
    sessions, tokens, token, data = await _confirmation(session_factory)
    data = data | {"cover_file_id": cover}
    await sessions.set(USER_ID, "draft:publish_confirm", data)
    async with session_factory() as session, session.begin():
        group = Group(chat_id=-1001, title="测试群", timezone="Asia/Shanghai")
        session.add(group)
        await session.flush()
        session.add(GroupAdmin(group_id=group.id, user_id=USER_ID, source="owner", active=True))
    await _preview(sessions, payload)
    stored = await sessions.get(USER_ID)
    assert stored.state == "draft:publish_confirm" and stored.data == data
    grant = await tokens.consume(token, owner_user_id=USER_ID, chat_id=USER_ID)
    assert grant.action == "draft_publish_confirm"
    publisher = CreatorPublisher(
        bot=object(),
        bot_username="lottery_test_bot",
        sessions=sessions,
        campaigns=CampaignService(partial(UnitOfWork, session_factory)),
        queries=QueryService(session_factory),
        profiles=SimpleNamespace(save_last_draft=AsyncMock()),
        renderer=object(),
    )
    # External permission lookup is isolated; persistence, confirmation and delivery are real code.
    monkeypatch.setattr(publisher, "_resolve", AsyncMock(return_value=data))
    monkeypatch.setattr("tg_lottery.telegram.creator_publication.edit_panel", AsyncMock())
    await publisher.publish(_query(), grant)
    async with session_factory() as session:
        card = (await session.scalars(select(TelegramOutbox))).one()
        assert card.operation == "send_campaign_card" and card.state == OutboxState.PENDING
        assert card.recipient_chat_id == -1001
    await _deliver_and_check(session_factory, cover)


async def _deliver_and_check(session_factory, cover):
    message = _query().message.model_copy(update={"chat": group_chat(), "message_id": 9001})
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=message),
        send_photo=AsyncMock(return_value=message),
        pin_chat_message=AsyncMock(return_value=True),
    )
    cipher = SecretCipher(urlsafe_b64encode(bytes(range(32))).decode())
    worker = OutboxWorker(
        owner_id="preview-regression",
        session_factory=session_factory,
        gateway=TelegramGateway(bot, cipher),
        cipher=cipher,
    )
    assert await worker.process_once()
    assert not await worker.process_once()
    send = bot.send_photo if cover else bot.send_message
    unused = bot.send_message if cover else bot.send_photo
    send.assert_awaited_once()
    unused.assert_not_awaited()
    assert send.await_args.args[0] == -1001
    bot.pin_chat_message.assert_awaited_once_with(-1001, 9001, disable_notification=True)
    async with session_factory() as session:
        card = (await session.scalars(select(TelegramOutbox))).one()
        campaign = (await session.scalars(select(Campaign))).one()
        assert card.state == OutboxState.CONFIRMED and card.remote_message_id == 9001
        assert campaign.published_message_id == 9001


def group_chat():
    from aiogram.types import Chat

    return Chat(id=-1001, type="supergroup")


async def test_preview_does_not_resume_an_already_suspended_draft(session_factory):
    sessions, tokens, token, _ = await _confirmation(session_factory)
    await suspend_draft(sessions, USER_ID)
    await _preview(sessions, "join_unpublished")
    assert (await sessions.get(USER_ID)).state == "draft:suspended"
    with pytest.raises(RecoverableDraftCallback, match="suspended"):
        await tokens.consume(token, owner_user_id=USER_ID, chat_id=USER_ID)
