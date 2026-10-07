from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_creator_gate_feedback import PANEL_ID, USER_ID, _message
from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.v30_helpers import FIRST
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Group
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_behavior_admission import CreatorBehaviorAdmission
from tg_lottery.telegram.creator_group_selection import toggle_group
from tg_lottery.telegram.draft_config import new_draft


def _creator(sessions, renderer):
    return CampaignCreator(
        bot=object(),
        bot_username="test_bot",
        sessions=sessions,
        campaigns=object(),
        queries=SimpleNamespace(
            group_by_chat_id=AsyncMock(
                return_value=Group(chat_id=FIRST, title="A群", timezone="Asia/Shanghai")
            )
        ),
        renderer=renderer,
        settings=object(),
        profiles=object(),
    )


def _query():
    return SimpleNamespace(from_user=SimpleNamespace(id=USER_ID), message=_message())


def _grant(action):
    return CallbackGrant(f"draft_gate:{action}", None, {})


@pytest.mark.parametrize("mode", ["message", "invite"])
async def test_empty_gate_save_stays_in_editor_with_explicit_error(mode):
    data = editor.apply_action(editor.open_editor(new_draft()), f"draft_gate:mode:{mode}")
    sessions = ThresholdSessions(data)
    sessions.state = "draft:behavior_admission"
    renderer = SimpleNamespace(render=AsyncMock())
    admission = CreatorBehaviorAdmission(sessions=sessions, renderer=renderer, bot=object())

    with pytest.raises(UserFacingError, match="请至少选择一个参与群并保存本群门槛"):
        await admission.handle(_query(), _grant("save"))

    assert sessions.state == "draft:behavior_admission"
    assert sessions.data == data
    assert sessions.data["admission"] is None
    renderer.render.assert_not_awaited()


async def test_choose_groups_then_resume_invite_editor_and_save(monkeypatch):
    sessions = ThresholdSessions(new_draft() | {"panel_message_id": PANEL_ID})
    renderer = RecordingRenderer()
    renderer.render_groups = AsyncMock()
    creator = _creator(sessions, renderer)
    query = _query()
    await creator.handle_grant(query, CallbackGrant("draft_gate", None, {}))
    await creator.handle_grant(query, _grant("mode:invite"))
    await creator.handle_grant(query, _grant("retention:24"))

    await creator.handle_grant(query, _grant("choose_groups"))
    renderer.render_groups.assert_awaited_once()
    assert sessions.state == "draft:idle"
    assert sessions.data[editor.EDITOR_KEY]["type"] == "invite"
    group = Group(chat_id=FIRST, title="A群", timezone="Asia/Shanghai")
    await sessions.set(USER_ID, "draft:idle", toggle_group(sessions.data, group))
    await creator.handle_grant(query, CallbackGrant("draft_groups_done", None, {}))
    await creator.handle_grant(query, CallbackGrant("draft_gate", None, {}))
    assert "当前选择\N{FULLWIDTH COLON}邀请好友" in renderer.text
    assert sessions.data[editor.EDITOR_KEY]["retention_hours"] == 24
    await creator.handle_grant(query, _grant(f"group:{FIRST}"))
    await creator.handle_grant(query, _grant("input:required_valid_invites"))
    assert await creator.handle_message(_message("5")) is True
    await creator.handle_grant(query, _grant("group_save"))
    verify = AsyncMock()
    monkeypatch.setattr(
        "tg_lottery.telegram.creator_behavior_admission.verify_publication_bonus_permissions",
        verify,
    )

    await creator.handle_grant(query, _grant("save"))

    assert sessions.state == "draft:idle"
    assert sessions.data["admission"] == {
        "type": "invite",
        "groups": {str(FIRST): {"required_valid_invites": 5}},
    }
    assert sessions.data["invite_retention_hours"] == 24
    assert editor.EDITOR_KEY not in sessions.data
    verify.assert_awaited_once()
