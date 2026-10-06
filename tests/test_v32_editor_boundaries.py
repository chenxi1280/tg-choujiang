from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tg_lottery.domain.errors import AuthorizationDenied, UserFacingError
from tg_lottery.telegram.campaign_authorization import verify_campaign_admin
from tg_lottery.telegram.creator_editor_cleanup import retain_group_editors
from tg_lottery.telegram.creator_retention_revision import (
    checked_retention_editor,
    resolve_retention,
    synchronize_retention,
)


def test_removed_group_cannot_be_saved_back_from_an_old_editor():
    data = {
        "admission_editor": {
            "groups": {"-1": {"daily_messages": 20}, "-2": {"daily_messages": 34}},
            "chat_id": "-1",
            "rule": {"daily_messages": 50},
            "field": "daily_messages",
            "copy_targets": ["-1", "-2"],
        },
        "bonus_working_copy": {"chat_id": -1, "kind": "invite"},
        "bonus_copy": {"source_chat_id": -1, "targets": [-2]},
    }
    original = deepcopy(data)
    kept = retain_group_editors(data, {"-2"})
    assert kept["admission_editor"]["groups"] == {"-2": {"daily_messages": 34}}
    assert kept["admission_editor"]["chat_id"] is None
    assert kept["admission_editor"]["copy_targets"] == ["-2"]
    assert "bonus_working_copy" not in kept and "bonus_copy" not in kept
    assert data == original


def test_unchanged_retention_snapshot_follows_new_saved_value_without_losing_group_edits():
    data = {
        "invite_retention_hours": 48,
        "admission_editor": {
            "retention_hours": 24,
            "retention_base": 24,
            "retention_modified": False,
            "groups": {"-1": {"required_valid_invites": 2}},
        },
    }
    updated = synchronize_retention(data)
    assert checked_retention_editor(updated)["retention_hours"] == 48
    assert updated["admission_editor"]["groups"] == data["admission_editor"]["groups"]
    assert data["admission_editor"]["retention_hours"] == 24


def test_two_explicit_retention_changes_require_a_choice_and_keep_other_fields():
    data = {
        "invite_retention_hours": 48,
        "admission_editor": {
            "retention_hours": 0,
            "retention_base": 24,
            "retention_modified": True,
            "groups": {"-1": {"required_valid_invites": 2}},
        },
    }
    conflicted = synchronize_retention(data)
    with pytest.raises(UserFacingError, match="选择"):
        checked_retention_editor(conflicted)
    resolved = resolve_retention(conflicted, use_saved=False)
    assert checked_retention_editor(resolved)["retention_hours"] == 0
    assert resolved["admission_editor"]["groups"] == data["admission_editor"]["groups"]


async def test_global_management_cannot_be_authorized_by_only_one_participating_group():
    campaign_id = uuid4()
    queries = SimpleNamespace(
        campaign=AsyncMock(return_value=SimpleNamespace(id=campaign_id, contract_version="v2.4")),
        qualification_targets=AsyncMock(
            return_value=(
                SimpleNamespace(kind="participation_group", chat_id=-1, title="A群"),
                SimpleNamespace(kind="participation_group", chat_id=-2, title="B群"),
            )
        ),
    )

    async def member(chat_id, user_id):
        return SimpleNamespace(
            status="member" if chat_id == -2 and user_id == 101 else "administrator"
        )

    bot = SimpleNamespace(id=9001, get_chat_member=AsyncMock(side_effect=member))
    with pytest.raises(AuthorizationDenied, match="B群"):
        await verify_campaign_admin(bot, queries, campaign_id=campaign_id, user_id=101)
    assert bot.get_chat_member.await_count == 4
