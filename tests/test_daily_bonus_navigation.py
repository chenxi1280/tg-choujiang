from copy import deepcopy

import pytest

from tests.test_creator_gate_navigation import PANEL_ID, _creator, _query
from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tests.test_v33_unlimited_creation import current_draft
from tests.v30_helpers import FIRST
from tg_lottery.application.callback_tokens import CallbackGrant


@pytest.mark.parametrize(
    "guard", [{}, {"publish_snapshot": {"rules": "frozen"}}, {"published_campaign_id": "existing"}]
)
@pytest.mark.parametrize("entry", ["draft_rules", "draft_back", "dashboard", "text_dashboard"])
async def test_main_bonus_navigation_upgrades_only_unfrozen_drafts(guard, entry):
    source = (
        current_draft()
        | {
            "bonus_policy_version": "unlimited-v1",
            "bonus_types": ["activity"],
            "creator_path": ["dashboard", "bonus", "bonus_activity"],
            "panel_message_id": PANEL_ID,
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
    creator, query = _creator(sessions, renderer), _query()
    if entry == "dashboard":
        await creator._render_dashboard(query.from_user.id, query.message, source)
    elif entry == "text_dashboard":
        await creator._render_dashboard_from_text(query.message, source)
    else:
        await creator.handle_grant(query, CallbackGrant(entry, None, {}))
    assert source == before
    if guard:
        assert sessions.data["bonus_policy_version"] == "unlimited-v1"
        assert sessions.data["group_activity_rules"] == before["group_activity_rules"]
        assert "不限" in renderer.text
    else:
        assert sessions.data["bonus_policy_version"] == "daily-after-start-v1"
        assert sessions.data["group_activity_rules"][str(FIRST)]["reward_tickets"] == 1
        assert "首日不发" in renderer.text and "整场每天最多1张" in renderer.text
