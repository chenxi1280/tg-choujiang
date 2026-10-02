from copy import deepcopy
from types import SimpleNamespace

from tests.test_creator_settings import (
    RecordingRenderer,
    RequiredChatBot,
    ThresholdSessions,
    _message,
    _panel_query,
)
from tg_lottery.telegram.creator_admission_picker import CreatorAdmissionPicker
from tg_lottery.telegram.creator_required_chats_data import with_required_chats
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.draft_config import rules_from_data


def test_replacing_admission_conditions_preserves_participation_group_thresholds():
    data = _draft() | {"participation_group_ids": [-1001, -1004]}
    data["chat_message_thresholds"] = data["chat_message_thresholds"] | {"-1004": 8}
    before = deepcopy(data)

    updated = with_required_chats(data, [])

    assert updated["chat_message_thresholds"] == {"-1001": 4, "-1004": 8}
    assert data == before
    assert rules_from_data(updated).message_thresholds == {"-1001": 4, "-1004": 8}


async def test_adding_admission_link_does_not_remove_main_group_threshold():
    sessions = ThresholdSessions(_draft())
    settings = _settings(sessions)

    await settings._receive_participation(_message("-1003"), sessions.data)

    assert sessions.data["required_chat_ids"] == [-1002, -1003]
    assert sessions.data["chat_message_thresholds"] == {"-1001": 4, "-1002": 7}


async def test_clearing_admission_removes_only_admission_thresholds():
    sessions = ThresholdSessions(_draft())
    settings = _settings(sessions)

    await settings._clear_participation(_panel_query(), None)

    assert sessions.data["required_chat_ids"] == []
    assert sessions.data["chat_message_thresholds"] == {"-1001": 4}


async def test_toggling_admission_off_keeps_existing_main_group_threshold():
    data = _draft()
    picker = CreatorAdmissionPicker(
        bot=RequiredChatBot(), sessions=object(), queries=object(), renderer=object()
    )

    updated = await picker._toggle(data, -1002, 201)

    assert updated["required_chat_ids"] == []
    assert updated["chat_message_thresholds"] == {"-1001": 4}
    assert data["chat_message_thresholds"] == {"-1001": 4, "-1002": 7}


def _draft():
    return {
        "chat_id": -1001,
        "panel_message_id": 9,
        "required_chats": [{"id": -1002, "title": "准入群", "type": "supergroup", "private": True}],
        "chat_message_thresholds": {"-1001": 4, "-1002": 7},
    }


def _settings(sessions):
    return CreatorSettings(
        sessions=sessions,
        renderer=RecordingRenderer(),
        profiles=object(),
        queries=SimpleNamespace(),
        bot=RequiredChatBot(),
    )
