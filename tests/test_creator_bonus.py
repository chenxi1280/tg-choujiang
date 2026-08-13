from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import (
    MAX_ACTIVITY_REWARD_TICKETS,
    MAX_INVITE_BONUS_TICKETS,
    ActivityRule,
    CampaignDraft,
    CampaignRules,
    InviteRewardTier,
    InviteRule,
)
from tg_lottery.telegram.creator_bonus import bonus_summary, parse_bonus_config


def test_bonus_config_supports_invite_tiers_and_daily_activity() -> None:
    changes = parse_bonus_config("邀请 1:1,2:3 上限3;发言 10:1")

    assert changes == {
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
    assert "1人+1次" in bonus_summary(changes)
    assert "每天10条+1次" in bonus_summary(changes)


def test_bonus_config_requires_an_invite_cap_and_increasing_tiers() -> None:
    with pytest.raises(UserFacingError, match="必须设置总上限"):
        parse_bonus_config("邀请 1:1,2:3")
    with pytest.raises(UserFacingError, match="必须递增"):
        parse_bonus_config("邀请 1:3,2:2 上限10")
    with pytest.raises(UserFacingError, match="最后一档必须等于总上限"):
        parse_bonus_config("邀请 1:1,2:3 上限10")


def test_bonus_config_can_be_disabled_explicitly() -> None:
    assert parse_bonus_config("关闭") == {"bonus_types": [], "luck_mode": "off"}


def test_bonus_config_rejects_zero_duplicate_and_excessive_values_immediately() -> None:
    with pytest.raises(UserFacingError, match="每日消息数必须"):
        parse_bonus_config("发言 0:0")
    with pytest.raises(UserFacingError, match="发言加成只能配置一次"):
        parse_bonus_config("发言 10:1;发言 20:2")
    with pytest.raises(UserFacingError, match="邀请加成只能配置一次"):
        parse_bonus_config("邀请 1:1 上限1;邀请 2:2 上限2")
    with pytest.raises(UserFacingError, match="邀请加成总上限不能超过"):
        parse_bonus_config(
            f"邀请 1:{MAX_INVITE_BONUS_TICKETS + 1} 上限{MAX_INVITE_BONUS_TICKETS + 1}"
        )


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
