from datetime import UTC, datetime, timedelta

import pytest

from tg_lottery.domain.draw import WeightedEntry, select_weighted
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import InvalidStateTransition
from tg_lottery.domain.rules import CampaignDraft, CampaignRules, canonical_hash
from tg_lottery.domain.transitions import require_campaign_transition


def test_campaign_timeline_and_hash_are_deterministic() -> None:
    start = datetime(2026, 8, 8, tzinfo=UTC)
    draft = CampaignDraft(
        group_id=-1001,
        name="测试抽奖",
        preheat_at=start,
        cutoff_at=start + timedelta(days=1),
        draw_at=start + timedelta(days=1, minutes=10),
        rules=CampaignRules(),
    )
    assert draft.rules.activity.message_threshold == 10
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_invalid_timeline_is_rejected() -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with pytest.raises(ValueError, match="timeline"):
        CampaignDraft(
            group_id=-1001,
            name="bad",
            preheat_at=now,
            cutoff_at=now,
            draw_at=now,
        )


def test_campaign_state_machine_rejects_shortcut() -> None:
    require_campaign_transition(CampaignState.RUNNING, CampaignState.SETTLING)
    with pytest.raises(InvalidStateTransition):
        require_campaign_transition(CampaignState.RUNNING, CampaignState.DRAWN)


def test_weighted_draw_is_deterministic_and_in_range() -> None:
    entries = (WeightedEntry("a", 1), WeightedEntry("b", 3))
    first = select_weighted(entries, "seed", 1)
    second = select_weighted(entries, "seed", 1)
    assert first == second
    assert 1 <= first.selected_value <= 4
    assert first.range_start <= first.selected_value <= first.range_end


def test_weighted_draw_requires_positive_weight() -> None:
    with pytest.raises(ValueError, match="positive weight"):
        select_weighted((WeightedEntry("a", 0),), "seed", 1)
