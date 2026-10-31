from copy import deepcopy

import pytest

from tests.test_v33_unlimited_creation import current_draft
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as gate
from tg_lottery.telegram import creator_group_bonus as bonus
from tg_lottery.telegram.creator_behavior_views import view as gate_view
from tg_lottery.telegram.creator_bonus_policy import current_bonus_policy
from tg_lottery.telegram.draft_config import rules_from_data


def test_double_count_setting_saves_and_cancel_preserves_rule():
    data = bonus.apply_action(current_draft(), "draft_bonus_activity_group:-100123")
    text, rows = bonus.editor_view(data, data["bonus_working_copy"])
    assert "首日不发" in text and "整场每天最多1张" in text
    assert any(action == "draft_bonus_double:off" for row in rows for _, action in row)
    data = bonus.apply_action(data, "draft_bonus_double:off")
    assert bonus.working_copy_dirty(data)
    cancelled = bonus.apply_action(data, "draft_bonus_group_cancel")
    assert cancelled["activity_bonus_double_count"]
    saved = bonus.apply_action(data, "draft_bonus_activity_save")
    rules = rules_from_data(saved)
    assert rules.daily_activity_bonus
    assert not rules.activity_bonus_double_count
    assert rules.activity_rule_for(-100123).daily_max_cycles == 1
    reopened = bonus.apply_action(saved, "draft_bonus_activity_group:-100123")
    assert "不双算" in bonus.editor_view(reopened, reopened["bonus_working_copy"])[0]


@pytest.mark.parametrize("action", ["draft_bonus_input_reward", "draft_bonus_activity_unlimited"])
def test_fixed_daily_reward_cannot_be_changed(action):
    data = bonus.apply_action(current_draft(), "draft_bonus_activity_group:-100123")
    with pytest.raises(UserFacingError, match="固定1张"):
        bonus.apply_action(data, action)


def test_period_gate_can_be_selected_and_preserved_in_frozen_rules():
    data = gate.open_editor(current_draft())
    data = gate.apply_action(data, "draft_gate:group:-100123")
    data = gate.apply_action(data, "draft_gate:count_mode:period")
    text, rows = gate_view(data)
    labels = [label for row in rows for label, _ in row]
    assert "本期累计" in text and "自定义本期累计条数" in labels
    assert not any("条/天" in label for label in labels)
    data = gate.apply_action(data, "draft_gate:preset:daily_messages:66")
    data = gate.apply_action(data, "draft_gate:group_save")
    data = gate.apply_action(data, "draft_gate:save")
    rule = rules_from_data(data).message_gate_rules["-100123"]
    assert rule.count_mode == "period" and rule.daily_messages == 66


def test_published_rules_and_bound_snapshots_are_not_converted():
    source = current_draft() | {"bonus_policy_version": "unlimited-v1"}
    for guard in ({"published_campaign_id": "existing"}, {"publish_snapshot": {"rules": "frozen"}}):
        frozen = source | guard
        before = deepcopy(frozen)
        assert current_bonus_policy(frozen) == before


def test_unpublished_import_replaces_obsolete_policy_note():
    previous = (
        "新场发言抽奖加成和邀请抽奖加成都不设上限\N{FULLWIDTH COMMA}"
        "邀请留存固定24小时\N{FULLWIDTH SEMICOLON}历史活动不变。"
    )
    source = current_draft() | {"import_notes": [previous, "保留群配置提示"]}
    converted = current_bonus_policy(source)
    assert previous not in converted["import_notes"]
    assert "保留群配置提示" in converted["import_notes"]
    assert any("首日不发" in value for value in converted["import_notes"])
    assert source["import_notes"][0] == previous
