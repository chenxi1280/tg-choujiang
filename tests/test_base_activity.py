from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from tests.v22_bonus_test_helpers import draft_data
from tg_lottery.domain.base_activity import base_progress
from tg_lottery.domain.rules import BaseActivityRule, CampaignRules
from tg_lottery.telegram.creator_base_activity import apply_base_rule
from tg_lottery.telegram.draft_config import rules_from_data


def test_seven_days_requires_each_day_and_retains_completed_qualification():
    start = date(2026, 9, 1)
    rule = BaseActivityRule()
    counts = {start + timedelta(days=index): 5 for index in range(7)}
    assert base_progress(counts, rule, start + timedelta(days=6)).qualified
    assert base_progress(counts, rule, start + timedelta(days=20)).qualified
    counts[start + timedelta(days=3)] = 4
    result = base_progress(counts, rule, start + timedelta(days=6))
    assert not result.qualified
    assert result.consecutive_days == 3


def test_missing_day_resets_streak_and_today_does_not_reset_until_day_is_over():
    start = date(2026, 9, 1)
    rule = BaseActivityRule()
    counts = {start: 5, start + timedelta(days=1): 5}
    assert base_progress(counts, rule, start + timedelta(days=2)).consecutive_days == 2
    assert base_progress(counts, rule, start + timedelta(days=3)).consecutive_days == 0


def test_configuration_is_per_group_immutable_and_freezes():
    original = draft_data()
    first = apply_base_rule(original, "1 7 5 5")
    both = apply_base_rule(first, "2 3 10 8")
    assert not original.get("base_activity_rules")
    rules = rules_from_data(both)
    assert rules.base_activity_rules["-1001"].consecutive_days == 7
    assert rules.base_activity_rules["-1002"].daily_messages == 10
    assert "-1001" not in apply_base_rule(both, "1 0")["base_activity_rules"]
    assert "-1001" in both["base_activity_rules"]


@pytest.mark.parametrize("value", ["1 0 5 5", "1 7 -1 5", "1 7 5.5 5", "3 7 5 5", "1 7"])
def test_invalid_configuration_is_not_saved(value):
    from tg_lottery.domain.errors import UserFacingError

    with pytest.raises(UserFacingError):
        apply_base_rule(draft_data(), value)


def test_invalid_rule_keys_rejected_and_old_campaigns_have_no_gate():
    assert not CampaignRules().base_activity_rules
    with pytest.raises(ValidationError):
        CampaignRules(base_activity_rules={"bad": BaseActivityRule()})
