from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tg_lottery.domain.rules import (
    MAX_ACTIVITY_REWARD_TICKETS,
    ActivityRule,
    CampaignDraft,
    CampaignRules,
    InviteRewardTier,
    InviteRule,
)
from tg_lottery.telegram.creator_bonus import (
    activity_bonus_rows,
    apply_bonus_action,
    bonus_rows,
    bonus_summary,
    invite_bonus_rows,
)


def test_bonus_configuration_is_discoverable_through_keyboard_buttons() -> None:
    data = _bonus_data()

    assert bonus_rows(data)[0] == [
        ("✅ 邀请加成", "draft_bonus_toggle_invite"),
        ("✅ 发言加成", "draft_bonus_toggle_activity"),
    ]
    assert any("邀请人数" in label for label, _ in invite_bonus_rows(data)[0])
    assert any("消息数" in label for label, _ in activity_bonus_rows(data)[0])
    assert "1人+1次" in bonus_summary(data)
    assert "每天10条+1次" in bonus_summary(data)


def test_invite_keyboard_edits_last_tier_and_keeps_cap_reachable() -> None:
    data = _bonus_data()

    changed = apply_bonus_action(data, "draft_bonus_invite_add")
    changed = apply_bonus_action(changed, "draft_bonus_invite_count_inc")
    changed = apply_bonus_action(changed, "draft_bonus_invite_total_inc")

    assert changed["invite_reward_tiers"][-1] == {"invite_count": 4, "total_bonus": 5}
    assert changed["invite_bonus_cap"] == 5
    assert data["invite_reward_tiers"][-1] == {"invite_count": 2, "total_bonus": 3}


def test_bonus_types_toggle_without_text_input() -> None:
    data = _bonus_data()

    invite_off = apply_bonus_action(data, "draft_bonus_toggle_invite")
    all_off = apply_bonus_action(invite_off, "draft_bonus_toggle_activity")

    assert invite_off["bonus_types"] == ["activity"]
    assert all_off["bonus_types"] == []
    assert all_off["luck_mode"] == "off"


def test_bonus_ranges_are_enforced_by_the_domain_contract() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        CampaignDraft(
            group_id=-1001,
            name="超范围发言加成",
            preheat_at=now,
            cutoff_at=now + timedelta(hours=1),
            draw_at=now + timedelta(hours=2),
            rules=CampaignRules(
                bonus_types=("activity",),
                activity=ActivityRule(reward_tickets=MAX_ACTIVITY_REWARD_TICKETS + 1),
            ),
        )


def test_historical_frozen_rules_remain_parseable_outside_new_campaign_limits() -> None:
    rules = CampaignRules.model_validate(
        {
            "luck_mode": "standard",
            "activity": {"reward_tickets": MAX_ACTIVITY_REWARD_TICKETS + 1},
        }
    )

    assert rules.activity.reward_tickets == MAX_ACTIVITY_REWARD_TICKETS + 1


def test_invite_tiers_require_a_reachable_cap_in_the_domain_contract() -> None:
    with pytest.raises(ValidationError, match="require max bonus tickets"):
        InviteRule(reward_tiers=(InviteRewardTier(invite_count=1, total_bonus=1),))


def _bonus_data() -> dict[str, object]:
    return {
        "bonus_types": ["invite", "activity"],
        "luck_mode": "standard",
        "invite_reward_tiers": [
            {"invite_count": 1, "total_bonus": 1},
            {"invite_count": 2, "total_bonus": 3},
        ],
        "invite_bonus_cap": 3,
        "activity_message_threshold": 10,
        "activity_reward_tickets": 1,
    }
