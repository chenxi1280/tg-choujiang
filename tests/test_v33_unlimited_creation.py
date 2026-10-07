from copy import deepcopy

import pytest
from pydantic import ValidationError

from tests.v22_bonus_test_helpers import draft_data
from tests.v30_helpers import FIRST, rules_for
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram import creator_group_bonus as bonus
from tg_lottery.telegram.creator_bonus_policy import current_bonus_policy
from tg_lottery.telegram.creator_invite_retention import apply_action as retention_action
from tg_lottery.telegram.creator_invite_retention import view as retention_view
from tg_lottery.telegram.draft_config import ensure_draft_identity, new_draft, rules_from_data


def current_draft():
    return new_draft() | {
        "participation_groups": [{"id": FIRST, "title": "A群"}],
        "admission": {"type": "message", "groups": {str(FIRST): {"daily_messages": 5}}},
    }


def test_new_creation_opens_and_saves_both_unlimited_bonuses():
    data = current_draft()
    for kind in ("activity", "invite"):
        data = bonus.apply_action(data, f"draft_bonus_{kind}_group:{FIRST}")
        _, rows = bonus.editor_view(data, data["bonus_working_copy"])
        assert not any(
            "上限" in label or "每天达标一次" in label for row in rows for label, _ in row
        )
        data = bonus.apply_action(data, f"draft_bonus_{kind}_save")
    rules = rules_from_data(data)
    assert rules.activity_rule_for(FIRST).daily_cap_mode == "unlimited"
    assert rules.activity_rule_for(FIRST).daily_max_cycles is None
    assert rules.invite_rule_for(FIRST).cap_mode == "unlimited"
    assert rules.invite_rule_for(FIRST).max_bonus_tickets is None
    assert set(rules.bonus_types) == {"activity", "invite"}
    assert rules.invite.retention_hours == 24


@pytest.mark.parametrize(
    "action",
    ["draft_bonus_activity_daily", "draft_bonus_activity_limited", "draft_bonus_preset:cap:9"],
)
def test_obsolete_daily_limit_buttons_cannot_restore_caps(action):
    data = bonus.apply_action(current_draft(), f"draft_bonus_activity_group:{FIRST}")
    before = deepcopy(data)
    with pytest.raises(UserFacingError, match="不设上限"):
        bonus.apply_action(data, action)
    assert data == before


def test_restore_unpublished_old_settings_normalizes_without_changing_source_or_rewards():
    source = current_draft() | {
        "bonus_policy_version": None,
        "invite_retention_hours": 48,
        "group_activity_rules": {
            str(FIRST): {
                "messages_per_cycle": 34,
                "reward_tickets": 3,
                "daily_cap_mode": "limited",
                "daily_max_cycles": 1,
            }
        },
    }
    before = deepcopy(source)
    converted = current_bonus_policy(source)
    rule = converted["group_activity_rules"][str(FIRST)]
    assert (rule["messages_per_cycle"], rule["reward_tickets"]) == (34, 3)
    assert rule["daily_cap_mode"] == "unlimited" and rule["daily_max_cycles"] is None
    assert converted["invite_retention_hours"] == 24
    assert "历史活动不变" in converted["import_notes"][-1]
    assert source == before
    assert current_bonus_policy(source | {"published_campaign_id": "published"}) == (
        source | {"published_campaign_id": "published"}
    )


def test_frozen_legacy_rule_keeps_limited_activity_and_nonstandard_retention():
    raw = rules_for("message", ("activity",)).model_dump(mode="json")
    raw["group_activity_rules"][str(FIRST)] |= {"daily_cap_mode": "limited", "daily_max_cycles": 1}
    raw["invite"]["retention_hours"] = 48
    old = CampaignRules.model_validate(raw)
    assert old.activity_rule_for(FIRST).daily_max_cycles == 1
    assert old.invite.retention_hours == 48
    with pytest.raises(ValidationError):
        CampaignRules.model_validate(raw | {"bonus_policy_version": "unlimited-v1"})


def test_current_retention_page_is_fixed_but_old_editing_path_still_reads_original_values():
    data = current_draft()
    text, rows = retention_view(data)
    assert "满 24 小时" in text
    assert len(rows) == 1
    with pytest.raises(UserFacingError, match="固定"):
        retention_action(data, "draft_retention_now")
    legacy = draft_data() | {"invite_retention_hours": 48}
    assert "48" in retention_view(legacy)[0]


def test_bound_publication_identity_does_not_import_current_policy_into_frozen_snapshot():
    frozen = {
        "draft_id": "existing-draft",
        "participation_rules_version": "v3.0",
        "invite_retention_hours": 48,
        "publish_snapshot": {"rules": "already-frozen"},
        "group_activity_rules": {str(FIRST): {"daily_cap_mode": "limited", "daily_max_cycles": 1}},
    }
    assert ensure_draft_identity(frozen) == frozen
