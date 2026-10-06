from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.v30_helpers import FIRST
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_behavior_admission import CreatorBehaviorAdmission
from tg_lottery.telegram.draft_config import new_draft

USER_ID = 101
PANEL_ID = 301


@pytest.mark.parametrize("mode,label", [("message", "发言数"), ("invite", "邀请好友")])
@pytest.mark.parametrize("has_groups", [False, True])
async def test_mode_callback_shows_selection_before_any_group_is_configured(
    mode, label, has_groups
):
    groups = [{"id": FIRST, "title": "A群"}] if has_groups else []
    sessions = ThresholdSessions(new_draft() | {"participation_groups": groups})
    renderer = RecordingRenderer()
    admission = CreatorBehaviorAdmission(sessions=sessions, renderer=renderer, bot=object())
    query = SimpleNamespace(from_user=SimpleNamespace(id=USER_ID), message=_message())
    await admission.show(query, None)
    initial_text, initial_rows = renderer.text, renderer.rows

    closed = await admission.handle(query, CallbackGrant(f"draft_gate:mode:{mode}", None, {}))

    assert not closed
    assert sessions.state == "draft:behavior_admission"
    assert sessions.data[editor.EDITOR_KEY]["type"] == mode
    assert sessions.data["admission"] is None
    assert renderer.text != initial_text and renderer.rows != initial_rows
    assert f"当前选择\N{FULLWIDTH COLON}{label}" in renderer.text
    assert (f"✅ {label}", f"draft_gate:mode:{mode}") in renderer.rows[0]
    if has_groups:
        assert "请点击下方群名" in renderer.text
        assert ("A群 · 不设门槛", f"draft_gate:group:{FIRST}") in renderer.rows[1]
    else:
        assert "选好群后回来设置门槛" in renderer.text
        assert ("选择参与活动的群", "draft_gate:choose_groups") in renderer.rows[1]


def _message(text: str = "") -> Message:
    return Message(
        message_id=PANEL_ID,
        date=0,
        chat=Chat(id=USER_ID, type="private"),
        from_user=User(id=USER_ID, is_bot=False, first_name="创建者"),
        text=text,
    )


def _input_data(mode: str = "message", field: str = "daily_messages") -> dict:
    data = new_draft() | {
        "participation_groups": [{"id": FIRST, "title": "A群"}],
        "panel_message_id": PANEL_ID,
    }
    for action in (
        f"draft_gate:mode:{mode}",
        f"draft_gate:group:{FIRST}",
        f"draft_gate:input:{field}",
    ):
        data = editor.apply_action(data, action)
    return data


@pytest.mark.parametrize(
    "mode,field,expected",
    [
        ("message", "daily_messages", "每天 34 条"),
        ("invite", "required_valid_invites", "新成员 34 人"),
    ],
)
async def test_custom_gate_input_is_handled_and_keeps_group_editor(mode, field, expected):
    sessions = ThresholdSessions(_input_data(mode, field))
    sessions.state = "draft:behavior_admission"
    renderer = RecordingRenderer()
    creator = CampaignCreator(
        bot=object(),
        bot_username="test_bot",
        sessions=sessions,
        campaigns=object(),
        queries=object(),
        renderer=renderer,
        settings=SimpleNamespace(handle_message=AsyncMock()),
        profiles=object(),
    )

    assert await creator.handle_message(_message("34")) is True
    assert sessions.state == "draft:behavior_admission"
    assert sessions.data[editor.EDITOR_KEY]["rule"][field] == 34
    assert sessions.data["admission"] is None
    assert expected in renderer.text
    assert any(("本群暂存到编辑稿", "draft_gate:group_save") in row for row in renderer.rows)


@pytest.mark.parametrize(
    "mode,field", [("message", "daily_messages"), ("invite", "required_valid_invites")]
)
async def test_invalid_custom_gate_input_preserves_staged_rule(mode, field):
    data = _input_data(mode, field)
    sessions = ThresholdSessions(data)
    renderer = SimpleNamespace(edit=AsyncMock())
    admission = CreatorBehaviorAdmission(sessions=sessions, renderer=renderer, bot=object())

    with pytest.raises(UserFacingError):
        await admission.receive(_message("不是数字"), data)

    assert sessions.data == data
    assert sessions.data[editor.EDITOR_KEY]["field"] == field
    renderer.edit.assert_not_awaited()
