from copy import deepcopy

import pytest

from tests.test_creator_gate_navigation import _creator, _query
from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.test_v33_unlimited_creation import current_draft
from tests.v30_helpers import FIRST
from tg_lottery.application.callback_tokens import CallbackGrant


@pytest.mark.parametrize(
    "guard", [{}, {"publish_snapshot": {"rules": "frozen"}}, {"published_campaign_id": "existing"}]
)
async def test_main_bonus_navigation_upgrades_only_unfrozen_drafts(guard):
    source = (
        current_draft()
        | {
            "bonus_policy_version": "unlimited-v1",
            "bonus_types": ["activity"],
            "group_activity_rules": {
                str(FIRST): {
                    "enabled": True,
                    "messages_per_cycle": 1,
                    "reward_tickets": 3,
                    "daily_cap_mode": "unlimited",
                    "daily_max_cycles": None,
                }
            },
        }
        | guard
    )
    before = deepcopy(source)
    sessions, renderer = ThresholdSessions(source), RecordingRenderer()
    await _creator(sessions, renderer).handle_grant(
        _query(), CallbackGrant("draft_rules", None, {})
    )
    assert source == before
    if guard:
        assert sessions.data["bonus_policy_version"] == "unlimited-v1"
        assert sessions.data["group_activity_rules"] == before["group_activity_rules"]
        assert "不限" in renderer.text
    else:
        assert sessions.data["bonus_policy_version"] == "daily-after-start-v1"
        assert sessions.data["group_activity_rules"][str(FIRST)]["reward_tickets"] == 1
        assert "首日不发" in renderer.text and "整场每天最多1张" in renderer.text
