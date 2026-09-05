from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_bonus import apply_bonus_action, bonus_summary, bonus_view
from tg_lottery.telegram.creator_bonus_permissions import verify_publication_bonus_permissions
from tg_lottery.telegram.creator_group_availability import inspect_group_availability


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
async def test_participation_picker_rejects_channels_before_selection(chat_type):
    bot = SimpleNamespace(
        id=9001,
        get_chat=AsyncMock(return_value=SimpleNamespace(type=chat_type)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
    )
    result = await inspect_group_availability(bot, -1001, 201)
    assert result.selectable is (chat_type != "channel")
    if chat_type == "channel":
        assert "准入条件" in result.reason


async def test_bonus_permission_error_identifies_group_by_name():
    bot = SimpleNamespace(
        id=9001,
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status="administrator", can_invite_users=False)
        ),
    )
    data = {
        "participation_groups": [{"id": -1001, "title": "验收一群"}],
        "group_invite_rules": {"-1001": {"enabled": True}},
    }
    with pytest.raises(UserFacingError) as caught:
        await verify_publication_bonus_permissions(bot, 201, data)
    assert "验收一群" in caught.value.public_message
    assert "-1001" not in caught.value.public_message
    assert "邀请权限" in caught.value.public_message


def test_enabled_bonus_without_group_rules_shows_pending_configuration():
    data = {
        "participation_groups": [{"id": -1001, "title": "验收一群"}],
        "bonus_types": ["invite"],
    }
    summary = bonus_summary(data)
    assert "邀请加成待逐群设置" in summary
    assert "每2个" not in summary
    assert "上限3次" not in summary


@pytest.mark.parametrize("kind", ["invite", "activity"])
def test_saving_group_bonus_returns_to_group_rules_instead_of_legacy_editor(kind):
    data = {"participation_groups": [{"id": -1001, "title": "验收一群"}]}
    editing = apply_bonus_action(data, f"draft_bonus_{kind}_group:-1001")
    saved = apply_bonus_action(editing, f"draft_bonus_{kind}_save")
    text, rows = bonus_view(saved, f"draft_bonus_{kind}_save")
    assert "逐群设置" in text
    assert "验收一群" in rows[0][0][0]
    assert rows[0][0][1] == f"draft_bonus_{kind}_group:-1001"
