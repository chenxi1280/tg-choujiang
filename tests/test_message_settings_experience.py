from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.types import Chat, Message

from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.test_template_schedule import _complete_draft
from tg_lottery.telegram.creator_messages import message_settings_rows, message_settings_text
from tg_lottery.telegram.creator_settings import CreatorSettings


@pytest.mark.parametrize("enabled", [True, False])
def test_explanation_toggle_is_reachable_and_shows_its_persisted_selection(enabled):
    data = _complete_draft() | {"message_tips": enabled}
    rows = message_settings_rows(data)
    assert {action for _, action in rows[0]} == {"draft_messages_on", "draft_messages_off"}
    selected = "draft_messages_on" if enabled else "draft_messages_off"
    assert [action for label, action in rows[0] if label.startswith("✅")] == [selected]
    assert "扩展说明" in message_settings_text(data)
    assert "领奖联系人" in message_settings_text(data)


@pytest.mark.parametrize(
    "action,enabled", [("draft_messages_off", False), ("draft_messages_on", True)]
)
async def test_explanation_action_saves_choice_and_displays_it_on_dashboard(action, enabled):
    sessions = ThresholdSessions(_complete_draft() | {"panel_message_id": 123})
    renderer = RecordingRenderer()
    settings = CreatorSettings(
        sessions=sessions, renderer=renderer, profiles=object(), queries=object(), bot=object()
    )
    message = Message(message_id=123, date=datetime.now(UTC), chat=Chat(id=101, type="private"))
    query = SimpleNamespace(from_user=SimpleNamespace(id=101), message=message)
    await settings._set_messages(query, SimpleNamespace(action=action))
    assert sessions.data["message_tips"] is enabled
    assert ("扩展说明 显示" if enabled else "扩展说明 精简") in renderer.text
