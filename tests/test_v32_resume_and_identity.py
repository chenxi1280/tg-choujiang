from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_lottery.domain.action_confirmation import ExpectedCampaign
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_group_identity import (
    normalize_draft_groups,
    remap_group_references,
)
from tg_lottery.telegram.creator_suspension import resume_view, suspend_draft
from tg_lottery.telegram.member_navigation_context import MemberPageContext
from tg_lottery.telegram.member_region import region_view
from tg_lottery.telegram.panel_pagination import text_pages


@pytest.mark.parametrize("changed", ["state", "revision"])
def test_confirmation_rejects_changed_state_or_revision(changed):
    campaign = SimpleNamespace(state=CampaignState.RUNNING, config_revision=2)
    expected = ExpectedCampaign(str(campaign.state), campaign.config_revision)
    if changed == "state":
        campaign.state = CampaignState.CANCELLED
    else:
        campaign.config_revision += 1
    with pytest.raises(UserFacingError, match="确认未执行"):
        expected.validate(campaign)


async def test_leaving_input_preserves_field_and_pending_prize_without_accepting_more_text():
    data = {"title": "本人草稿", "pending_prize_name": "尚未提交奖品", "prizes": []}
    session = SimpleNamespace(state="draft:prize_count", data=data)
    sessions = SimpleNamespace(get=AsyncMock(return_value=session), set=AsyncMock())
    await suspend_draft(sessions, 101)
    user, state, updated = sessions.set.await_args.args
    assert user == 101 and state == "draft:suspended"
    assert updated["suspended_state"] == "draft:prize_count"
    assert updated["pending_prize_name"] == data["pending_prize_name"]
    assert data == session.data
    _, rows = resume_view(updated)
    assert ("继续原字段输入", "draft_resume_input") in [item for row in rows for item in row]


@pytest.mark.parametrize("state", ["draft:suspended", "draft:publishing", "profile:region"])
async def test_suspension_does_not_replace_existing_destination_or_publication(state):
    sessions = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(state=state, data={})), set=AsyncMock()
    )
    await suspend_draft(sessions, 101)
    sessions.set.assert_not_awaited()


def test_group_migration_remaps_only_target_keys_and_preserves_other_group_editing():
    data = {
        "participation_groups": [
            {"id": -1001, "title": "同名群"},
            {"id": -1002, "title": "同名群"},
        ],
        "participation_group_ids": [-1001, -1002],
        "admission": {"type": "message", "groups": {"-1001": {"daily_messages": 3}}},
        "admission_editor": {
            "chat_id": "-1001",
            "copy_targets": ["-1002"],
            "groups": {"-1001": {"daily_messages": 5}},
        },
        "title": "-1001",
        "group_id": "stable-group",
    }
    original = deepcopy(data)
    updated = remap_group_references(data, {"-1001": "-1009"})
    assert data == original
    assert updated["participation_group_ids"] == [-1009, -1002]
    assert updated["admission_editor"]["chat_id"] == "-1009"
    assert updated["admission_editor"]["copy_targets"] == ["-1002"]
    assert updated["admission"]["groups"] == {"-1009": {"daily_messages": 3}}
    assert updated["title"] == "-1001" and updated["group_id"] == "stable-group"


async def test_group_normalization_rejects_another_stable_identity():
    group = SimpleNamespace(id="other", chat_id=-1001)
    queries = SimpleNamespace(group_by_chat_id=AsyncMock(return_value=group))
    data = {"participation_groups": [{"id": -1001, "title": "同名群", "group_id": "original"}]}
    with pytest.raises(UserFacingError, match="身份已变化"):
        await normalize_draft_groups(queries, data)


def test_utf16_paging_preserves_all_emoji_and_text_without_truncation():
    text = "🧪验收" * 3000
    pages = text_pages(text)
    assert len(pages) > 1 and "".join(pages) == text
    assert all(len(page.encode("utf-16-le")) // 2 <= 4096 for page in pages)


async def test_locked_region_shows_deadline_and_original_activity_return_without_writes():
    now = datetime.now(UTC)
    profile = SimpleNamespace(
        region="北京",
        region_editable_until=now - timedelta(minutes=1),
        region_locked_until=now + timedelta(days=1),
    )
    profiles = SimpleNamespace(get=AsyncMock(return_value=profile))
    tokens = SimpleNamespace(create_many=AsyncMock())
    context = MemberPageContext("m:region:activity", ("m:conditions:activity:0",))
    text, keyboard = await region_view(profiles, tokens, user_id=101, context=context)
    assert "锁定至" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "m:conditions:activity:0"
    tokens.create_many.assert_not_awaited()


def test_publish_review_resume_returns_to_module_instead_of_accepting_an_arbitrary_field():
    from tg_lottery.telegram.creator_resume_views import has_field_input

    assert not has_field_input("draft:publish_confirm", {})
    _, rows = resume_view({"suspended_state": "draft:publish_confirm"})
    assert not any(action == "draft_resume_input" for row in rows for _, action in row)


def test_cancel_resumed_gate_input_preserves_staged_group_values():
    from tg_lottery.telegram.creator_behavior_editor import open_editor
    from tg_lottery.telegram.creator_resume_views import resumed_view
    from tg_lottery.telegram.draft_config import new_draft

    data = open_editor(new_draft())
    editor = data["admission_editor"] | {"type": "invite", "field": "search", "search": "A群"}
    data = data | {"admission_editor": editor}
    updated, state, _ = resumed_view(data, state="draft:behavior_admission", module=True)
    assert state == "draft:behavior_admission"
    assert updated["admission_editor"]["field"] is None
    assert updated["admission_editor"]["type"] == "invite"
    assert updated["admission_editor"]["search"] == "A群"
    assert data["admission_editor"]["field"] == "search"


def test_extra_conditions_are_a_real_resume_parent():
    from tg_lottery.telegram.creator_sections import section_view
    from tg_lottery.telegram.draft_config import new_draft

    text, rows = section_view(new_draft(), "requirements")
    assert "参与条件" in text or "准入条件" in text
    assert any(action == "draft_back" for row in rows for _, action in row)
