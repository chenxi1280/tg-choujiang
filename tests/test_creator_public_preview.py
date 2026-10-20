from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_template_schedule import _complete_draft
from tg_lottery.presentation.participation_rules import rule_manifest
from tg_lottery.telegram.bot_permission_reminders import send_bot_permission_reminder
from tg_lottery.telegram.creator_public_preview import preview_payload, send_public_preview
from tg_lottery.telegram.gateway import _campaign_card_text, _campaign_keyboard
from tg_lottery.telegram.home_views import creation_entry
from tg_lottery.telegram.start_payloads import unpublished_start
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


@pytest.mark.parametrize("cover", [None, "saved-photo"])
@pytest.mark.parametrize("description", ["活动说明", "很长的活动说明📚" * 600])
async def test_preview_uses_public_card_text_pages_media_and_button_labels(cover, description):
    data = _complete_draft() | {"cover_file_id": cover, "description": description}
    original = dict(data)
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    message = SimpleNamespace(chat=SimpleNamespace(id=101), answer=AsyncMock())
    await send_public_preview(bot, message, data=data, bot_username="test_bot")
    payload = preview_payload(data, "test_bot")
    text, pages = rule_manifest(payload)
    assert text == _campaign_card_text(payload)
    assert data == original
    for index, page in enumerate(pages, start=1):
        assert bot.send_message.await_args_list[index - 1].args[1] == (
            f"完整规则 {index}/{len(pages)}\n{page}"
        )
    call = bot.send_photo.await_args if cover else bot.send_message.await_args
    assert (call.kwargs["caption"] if cover else call.args[1]) == text
    assert call.kwargs["parse_mode"] == "HTML"
    if cover:
        assert call.args == (101, cover)
        assert call.kwargs["show_caption_above_media"] is False
    else:
        bot.send_photo.assert_not_awaited()
        assert call.kwargs["link_preview_options"].is_disabled
    _assert_preview_keyboard(call, payload)


def _assert_preview_keyboard(call, payload):
    rows = call.kwargs["reply_markup"].inline_keyboard
    assert [[b.text for b in row] for row in rows] == [
        [b.text for b in row] for row in _campaign_keyboard(payload).inline_keyboard
    ]
    assert all(
        b.callback_data == "preview:unpublished" and b.url is None for row in rows for b in row
    )


async def test_new_group_admin_has_private_creation_entry():
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )
    event = SimpleNamespace(
        chat=SimpleNamespace(id=-1001, type="supergroup"),
        old_chat_member=SimpleNamespace(status="member"),
        new_chat_member=SimpleNamespace(status="administrator"),
    )
    await send_bot_permission_reminder(bot, event)
    button = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.url == "https://t.me/test_bot?start=create"
    assert "发起抽奖" in button.text
    assert creation_entry(None) == ("发起抽奖", "nav:create")
    handler = object.__new__(LotteryUpdateHandler)
    handler._admin_flow = SimpleNamespace(start_create_message=AsyncMock())
    message = object()
    assert await handler._start_payload(message, "create")
    handler._admin_flow.start_create_message.assert_awaited_once_with(message)


@pytest.mark.parametrize(
    "payload", ["join_unpublished", "rules_unpublished", "condition_unpublished_0"]
)
async def test_preview_links_explain_unpublished_without_opening_activity(payload):
    message = SimpleNamespace(answer=AsyncMock())
    assert await unpublished_start(message, payload)
    assert "未发布" in message.answer.await_args.args[0]
    assert not await unpublished_start(message, "join_real_activity")


async def test_confirmation_binds_new_panel_after_preview_and_preserves_cover(monkeypatch):
    from tests.test_creator_cover import _message
    from tests.test_creator_settings import ThresholdSessions
    from tg_lottery.telegram import creator_publication as publication

    data = _complete_draft() | {"cover_file_id": "saved-photo", "panel_message_id": 9}
    sessions = ThresholdSessions(data)
    publisher = object.__new__(publication.CreatorPublisher)
    publisher._sessions = sessions
    publisher._queries = SimpleNamespace(campaign_for_draft=AsyncMock(return_value=None))
    publisher._bot, publisher._bot_username = object(), "test_bot"
    publisher._required_draft = AsyncMock(return_value=data)
    publisher._resolve = AsyncMock(return_value=data)
    publisher._renderer = SimpleNamespace(render=AsyncMock())
    monkeypatch.setattr(publication, "normalize_draft_groups", AsyncMock(return_value=data))
    preview = AsyncMock()
    monkeypatch.setattr(publication, "send_public_preview", preview)
    panel = _message(message_id=20)
    monkeypatch.setattr(type(panel), "answer", AsyncMock(return_value=panel))
    query = SimpleNamespace(from_user=panel.from_user, message=_message())
    await publisher.confirm(query, None)
    preview.assert_awaited_once()
    assert sessions.state == "draft:publish_confirm"
    assert sessions.data["panel_message_id"] == 20
    assert sessions.data["cover_file_id"] == "saved-photo"
    assert sessions.data["draft_id"] == data["draft_id"]
    assert "published_campaign_id" not in sessions.data
    assert publisher._renderer.render.await_args.args[0] == panel
