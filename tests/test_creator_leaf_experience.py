from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from tests.test_creator_settings import RecordingRenderer, RecordingSessions
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_folder_link import CreatorFolderLink, validated_folder_link
from tg_lottery.telegram.creator_prizes import CreatorPrizes
from tg_lottery.telegram.draft_config import new_draft, presentation_from_data
from tg_lottery.telegram.gateway import _campaign_keyboard

FOLDER_LINK = "https://t.me/addlist/test_Lottery-20260905"


def _message() -> Message:
    return Message(
        message_id=8,
        date=datetime.now(UTC),
        chat=Chat(id=201, type="private"),
        from_user=User(id=201, is_bot=False, first_name="测试管理"),
        text=FOLDER_LINK,
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/addlist/abc",
        "https://t.me/addlist/a?x=1",
        "https://t.me/addlist/",
        "https://t.me/+invite",
        "javascript:alert(1)",
    ],
)
def test_folder_link_rejects_non_telegram_folder_links(value) -> None:
    with pytest.raises(UserFacingError, match="组合分享链接"):
        validated_folder_link(value)


async def test_folder_link_preserves_all_independent_participation_conditions() -> None:
    sessions, renderer = RecordingSessions(), RecordingRenderer()
    data = new_draft() | {
        "panel_message_id": 9,
        "required_chat_ids": [-1001, -1002],
        "participation_group_ids": [-1003, -1004],
    }
    await CreatorFolderLink(sessions=sessions, renderer=renderer).receive(_message(), data)
    frozen = presentation_from_data(sessions.data)
    assert frozen["folder_link"] == FOLDER_LINK
    assert frozen["required_chat_ids"] == [-1001, -1002]
    assert frozen["participation_group_ids"] == [-1003, -1004]
    assert data["folder_link"] == ""
    assert "仍逐项核验" in renderer.text
    keyboard = _campaign_keyboard(
        {
            "campaign_ref": "reference",
            "bot_username": "lottery_test_bot",
            "presentation": frozen,
        }
    )
    assert keyboard.inline_keyboard[0][0].url.endswith("?start=join_reference")
    assert keyboard.inline_keyboard[-1][0].url == FOLDER_LINK


async def test_delete_specific_prizes_preserves_remaining_order_and_draft() -> None:
    sessions, renderer = RecordingSessions(), RecordingRenderer()
    creator = CreatorPrizes(sessions=sessions, renderer=renderer)
    data = {
        "panel_message_id": 9,
        "prizes": [
            {"name": "第一奖", "winner_count": 1},
            {"name": "第二奖", "winner_count": 2},
            {"name": "第三奖", "winner_count": 3},
            {"name": "第四奖", "winner_count": 4},
        ],
    }
    await creator.receive_delete(_message().model_copy(update={"text": "3,1"}), data)
    assert len(sessions.data["prizes"]) == 4
    sessions.get = AsyncMock(return_value=SimpleNamespace(state="draft:idle", data=sessions.data))
    await creator.commit_delete(SimpleNamespace(message=_message(), from_user=_message().from_user))
    assert [p["name"] for p in sessions.data["prizes"]] == ["第二奖", "第四奖"]
    assert len(data["prizes"]) == 4
    with pytest.raises(UserFacingError, match="编号必须"):
        await creator.receive_delete(_message().model_copy(update={"text": "9"}), data)
