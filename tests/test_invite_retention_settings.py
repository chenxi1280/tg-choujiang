from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from pydantic import ValidationError

from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.domain.rules import InviteRule, invite_due_at
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.creator_bonus import (
    BONUS_ACTIONS,
    apply_bonus_action,
    bonus_rows,
    bonus_view,
)
from tg_lottery.telegram.draft_config import new_draft, presentation_from_data, rules_from_data


@pytest.mark.parametrize(
    "action,hours",
    [
        ("draft_retention_now", 0),
        ("draft_retention_day", 24),
        ("draft_retention_two_days", 48),
    ],
)
def test_explicit_retention_choice_freezes_without_changing_draft(action, hours) -> None:
    original = new_draft() | {"bonus_types": ["invite"]}
    assert action in BONUS_ACTIONS
    changed = apply_bonus_action(original, action)
    rules = rules_from_data(changed)
    assert rules.invite.retention_hours == hours
    assert original["invite_retention_hours"] is None
    assert presentation_from_data(changed)["invite_retention_hours"] == hours
    text, _ = bonus_view(changed, action)
    assert "无需留存等待" in text if hours == 0 else f"{hours} 小时" in text
    joined = datetime.now(UTC)
    assert invite_due_at(joined, rules.invite) == joined + timedelta(hours=hours)
    assert any(action == "draft_retention" for row in bonus_rows(original) for _, action in row)


def test_existing_retention_preset_is_preserved_and_negative_rejected() -> None:
    assert rules_from_data(new_draft()).invite.retention_hours == 24
    assert rules_from_data(new_draft() | {"invite_preset": "strict"}).invite.retention_hours == 48
    with pytest.raises(ValidationError):
        InviteRule(retention_hours=-1)


async def test_last_draft_keeps_multi_group_rules_and_retention_without_runtime_identity(
    session_factory,
):
    service = UserProfileService(partial(UnitOfWork, session_factory))
    business = {
        "participation_group_ids": [-1001, -1002],
        "participation_groups": [{"id": -1001, "title": "第一群"}],
        "group_invite_rules": {"-1001": {"enabled": True, "invites_per_cycle": 3}},
        "group_activity_rules": {"-1002": {"enabled": True, "messages_per_cycle": 7}},
        "invite_retention_hours": 0,
        "folder_link": "https://t.me/addlist/test_folder",
        "chat_message_thresholds": {"-1002": 12},
        "invite_reward_mode": "ratio",
        "valid_invites_per_ticket": 3,
        "invite_cap_mode": "unlimited",
    }
    runtime = {
        "draft_id": "old-draft",
        "panel_message_id": 9,
        "published_campaign_id": "old-campaign",
    }
    now = datetime.now(UTC)
    await service.save_last_draft(201, business | runtime, now)
    assert await service.recent_last_draft(201, now) == business
