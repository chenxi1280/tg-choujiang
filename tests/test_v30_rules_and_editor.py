from datetime import timedelta

import pytest
from pydantic import ValidationError

from tests.v30_helpers import FIRST, SECOND, START, rules_for
from tg_lottery.domain.admission import validate_admission_window
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.rule_capabilities import require_capabilities
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram.creator_reuse import reusable_v3_draft
from tg_lottery.telegram.draft_config import new_draft


@pytest.mark.parametrize(
    "gate",
    [
        None,
        {"type": "message", "groups": {}},
        {"type": "both", "groups": {str(FIRST): {}}},
        {"type": "message", "groups": {str(FIRST): {"required_valid_invites": 2}}},
        {"type": "invite", "groups": {str(FIRST): {"daily_messages": 20}}},
    ],
)
def test_rejects_missing_or_mixed_admission(gate):
    with pytest.raises(ValidationError):
        CampaignRules(participation_rules_version="v3.0", admission=gate)


@pytest.mark.parametrize("hours", [0, 24, 48])
def test_invite_window_exact_deadline(hours):
    rules = rules_for("invite").model_copy(
        update={"invite": rules_for("invite").invite.model_copy(update={"retention_hours": hours})}
    )
    cutoff = START + timedelta(hours=hours)
    validate_admission_window(rules, start_at=START, cutoff_at=cutoff)
    with pytest.raises(ValueError):
        validate_admission_window(rules, start_at=START + timedelta(seconds=1), cutoff_at=cutoff)


def test_multi_day_window_requires_each_group():
    rules = rules_for(
        "message",
        groups={str(FIRST): dict(consecutive_days=1), str(SECOND): dict(consecutive_days=3)},
    )
    with pytest.raises(ValueError):
        validate_admission_window(rules, start_at=START, cutoff_at=START + timedelta(days=1))
    validate_admission_window(rules, start_at=START, cutoff_at=START + timedelta(days=2))


def draft():
    return new_draft() | {
        "participation_groups": [{"id": FIRST, "title": "A群"}, {"id": SECOND, "title": "B群"}]
    }


def test_group_edits_and_mode_replace_require_confirmation_and_discard_preserves_saved():
    data = editor.open_editor(draft())
    data = editor.apply_action(data, "draft_gate:mode:message")
    data = editor.apply_action(data, f"draft_gate:group:{FIRST}")
    data = editor.apply_action(data, "draft_gate:preset:daily_messages:34")
    attempted = editor.apply_action(data, "draft_gate:list")
    assert attempted[editor.EDITOR_KEY]["pending_action"] == "draft_gate:list"
    data = editor.apply_action(attempted, "draft_gate:keep_editing")
    data = editor.apply_action(data, "draft_gate:group_save")
    saved = editor.apply_action(data, "draft_gate:save")
    stage = editor.apply_action(editor.open_editor(saved), "draft_gate:mode:invite")
    assert stage["admission"]["type"] == "message"
    assert stage[editor.EDITOR_KEY]["pending_action"] == "draft_gate:mode:invite"
    assert editor.clear(stage)["admission"] == saved["admission"]
    switched = editor.apply_action(stage, "draft_gate:confirm_change")
    assert switched[editor.EDITOR_KEY]["type"] == "invite"
    assert switched[editor.EDITOR_KEY]["groups"] == {}


def test_copy_changes_only_selected_gate_targets():
    data = editor.open_editor(draft() | {"bonus_types": ["activity"]})
    for action in (
        "draft_gate:mode:message",
        f"draft_gate:group:{FIRST}",
        "draft_gate:preset:daily_messages:34",
        "draft_gate:copy_open",
    ):
        data = editor.apply_action(data, action)
    data = editor.toggle_copy(data, str(SECOND))
    data = editor.apply_action(data, "draft_gate:copy_confirm")
    assert data[editor.EDITOR_KEY]["groups"][str(SECOND)]["daily_messages"] == 34
    assert FIRST not in data[editor.EDITOR_KEY]["groups"]
    assert data["bonus_types"] == ["activity"]


def test_reuse_legacy_does_not_invent_gate_and_capability_guard_rejects_old_image():
    reused = reusable_v3_draft(
        draft()
        | {
            "invite_cap_mode": "limited",
            "invite_bonus_cap": 10,
            "base_activity_rules": {str(FIRST): {"daily_messages": 5}},
        }
    )
    assert reused["admission"] is None
    assert reused["invite_bonus_cap"] is None
    assert any("首日不发、次日起整场每天最多1张" in note for note in reused["import_notes"])
    with pytest.raises(RuntimeError, match=r"v3\.0"):
        require_capabilities(["legacy", "v3.0"], ["legacy"])
    require_capabilities(["legacy", "v3.0"], ["legacy", "v3.0"])


def test_legacy_retention_edits_are_staged_and_discard_keeps_shared_saved_requirement():
    data = draft() | {"invite_retention_hours": 24, "bonus_policy_version": None}
    opened = editor.open_editor(data)
    changed = opened | {editor.EDITOR_KEY: opened[editor.EDITOR_KEY] | {"retention_hours": 0}}
    assert editor.dirty(changed)
    assert editor.clear(changed)["invite_retention_hours"] == 24
    changed = editor.apply_action(changed, "draft_gate:mode:invite")
    changed = editor.apply_action(changed, f"draft_gate:group:{FIRST}")
    changed = editor.apply_action(changed, "draft_gate:group_save")
    assert editor.apply_action(changed, "draft_gate:save")["invite_retention_hours"] == 0
