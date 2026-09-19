from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, PhotoSize, User

from tests.test_creator_settings import ThresholdSessions
from tests.test_template_schedule import _complete_draft
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_settings import CreatorSettings


def _message(*, file_id=None, message_id=10):
    photos = (
        [
            PhotoSize(file_id=value, file_unique_id=value, width=size, height=size)
            for value, size in [("thumbnail", 100), (file_id, 800)]
        ]
        if file_id
        else None
    )
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=101, type="private"),
        from_user=User(id=101, is_bot=False, first_name="Creator"),
        photo=photos,
    )


class RecordingRenderer:
    """Record the panel rendering calls without a live Telegram connection."""

    def __init__(self) -> None:
        self.render = AsyncMock()
        self.edit = AsyncMock()
        self.clear_panel = AsyncMock()
        self.keyboard_for = AsyncMock(return_value="KEYBOARD")


@pytest.fixture
def flow():
    sessions = ThresholdSessions(_complete_draft() | {"panel_message_id": 9})
    renderer = RecordingRenderer()
    settings = CreatorSettings(
        sessions=sessions, renderer=renderer, profiles=object(), queries=object(), bot=object()
    )
    creator = CampaignCreator(
        sessions=sessions,
        renderer=renderer,
        settings=settings,
        bot=object(),
        bot_username="test_bot",
        campaigns=object(),
        queries=object(),
        profiles=object(),
    )
    return SimpleNamespace(sessions=sessions, renderer=renderer, creator=creator)


async def _upload(flow, monkeypatch, *, file_id="new-cover", panel_id=20):
    panel = _message(message_id=panel_id)
    answer = AsyncMock(return_value=panel)
    monkeypatch.setattr(Message, "answer", answer)
    edit = AsyncMock()
    monkeypatch.setattr(Message, "edit_reply_markup", edit)
    handled = await flow.creator.handle_text(
        _message(file_id=file_id), "draft:cover", flow.sessions.data
    )
    assert handled
    return SimpleNamespace(answer=answer, panel=panel, edit=edit)


async def test_upload_sends_success_with_buttons_on_new_panel(flow, monkeypatch):
    original = dict(flow.sessions.data)
    result = await _upload(flow, monkeypatch)
    assert "图片上传成功" in result.answer.call_args.args[0]
    assert flow.sessions.data["cover_file_id"] == "new-cover"
    assert flow.sessions.data["panel_message_id"] == 20
    assert flow.sessions.state == "draft:cover"
    assert original.get("cover_file_id") is None
    target = flow.renderer.keyboard_for.call_args.args[0]
    assert target.message_id == 20
    labels = {label for row in flow.renderer.keyboard_for.call_args.args[1] for label, _ in row}
    assert {"清除封面", "✅ 完成"} <= labels


async def test_upload_uses_single_edit_to_attach_the_keyboard(flow, monkeypatch):
    """The success text must not be sent and then immediately rewritten."""
    result = await _upload(flow, monkeypatch)
    assert result.answer.await_count == 1
    assert result.edit.await_count == 1
    assert result.edit.call_args.args == ()
    assert result.edit.call_args.kwargs["reply_markup"] == "KEYBOARD"
    assert flow.renderer.render.await_count == 0


async def test_sending_another_photo_replaces_cover_without_leaving_input(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    result = await _upload(flow, monkeypatch, file_id="replacement", panel_id=21)
    assert "图片替换成功" in result.answer.call_args.args[0]
    assert flow.sessions.data["cover_file_id"] == "replacement"
    assert flow.sessions.state == "draft:cover"
    assert flow.sessions.data["panel_message_id"] == 21


async def test_confirmation_returns_to_preview_preserving_cover(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    rows = flow.renderer.keyboard_for.call_args.args[1]
    action = next(action for row in rows for label, action in row if label == "✅ 完成")
    message = _message(message_id=20)
    query = SimpleNamespace(from_user=message.from_user, message=message)
    await flow.creator.handle_grant(query, SimpleNamespace(action=action))
    assert flow.sessions.state == "draft:idle"
    assert flow.sessions.data["cover_file_id"] == "new-cover"
    assert "封面设置：\u3010已设置\u3011" in flow.renderer.render.call_args.kwargs["text"]  # noqa: RUF001


async def test_cover_buttons_offer_no_duplicate_or_noop_actions(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    rows = flow.renderer.keyboard_for.call_args.args[1]
    actions = [action for row in rows for _, action in row]
    assert len(actions) == len(set(actions)), actions
    assert "draft_cover" not in actions


async def test_dashboard_shows_replace_label_once_cover_is_set(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    message = _message(message_id=20)
    query = SimpleNamespace(from_user=message.from_user, message=message)
    await flow.creator.handle_grant(query, SimpleNamespace(action="draft_back"))
    labels = {label for row in flow.renderer.render.call_args.kwargs["rows"] for label, _ in row}
    assert "替换封面" in labels


async def test_non_photo_is_rejected_without_changing_cover(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    original = dict(flow.sessions.data)
    with pytest.raises(UserFacingError, match="封面必须发送 Telegram 图片"):
        await flow.creator.handle_text(_message(), "draft:cover", flow.sessions.data)
    assert flow.sessions.data == original


async def test_clear_cover_returns_to_dashboard(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    message = _message(message_id=20)
    query = SimpleNamespace(from_user=message.from_user, message=message)
    await flow.creator.handle_grant(query, SimpleNamespace(action="draft_cover_clear"))
    assert flow.sessions.state == "draft:idle"
    assert flow.sessions.data["cover_file_id"] is None
    labels = {label for row in flow.renderer.render.call_args.kwargs["rows"] for label, _ in row}
    assert "上传封面" in labels


async def test_cover_is_saved_before_success_message_is_sent(flow, monkeypatch):
    async def answer(text):
        assert flow.sessions.data["cover_file_id"] == "new-cover"
        return _message(message_id=20)

    monkeypatch.setattr(Message, "answer", AsyncMock(side_effect=answer))
    monkeypatch.setattr(Message, "edit_reply_markup", AsyncMock())
    await flow.creator.handle_text(_message(file_id="new-cover"), "draft:cover", flow.sessions.data)


async def test_deleted_old_panel_does_not_fail_a_successful_upload(flow, monkeypatch):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText

    flow.renderer.clear_panel.side_effect = TelegramBadRequest(
        method=EditMessageText(chat_id=101, message_id=9, text="retired"),
        message="Bad Request: message to edit not found",
    )
    await _upload(flow, monkeypatch)
    assert flow.sessions.data["cover_file_id"] == "new-cover"


async def test_stale_panel_callback_does_not_take_over_latest_cover_panel(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    before = dict(flow.sessions.data)
    old_panel = _message(message_id=9)
    query = SimpleNamespace(from_user=old_panel.from_user, message=old_panel)
    with pytest.raises(UserFacingError, match="最新面板"):
        await flow.creator.recover_expired_panel(query)
    assert flow.sessions.data == before
    assert flow.sessions.state == "draft:cover"


async def test_save_failure_does_not_send_success(flow, monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    flow.sessions.set = AsyncMock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        await flow.creator.handle_text(
            _message(file_id="new-cover"), "draft:cover", flow.sessions.data
        )
    answer.assert_not_awaited()


async def test_expired_buttons_on_current_panel_can_still_recover(flow, monkeypatch):
    await _upload(flow, monkeypatch)
    panel = _message(message_id=20)
    query = SimpleNamespace(from_user=panel.from_user, message=panel)
    assert await flow.creator.recover_expired_panel(query)
    assert flow.sessions.data["panel_message_id"] == 20
    assert flow.sessions.data["cover_file_id"] == "new-cover"
    assert flow.sessions.state == "draft:idle"
