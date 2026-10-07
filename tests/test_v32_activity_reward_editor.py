from copy import deepcopy

import pytest
from sqlalchemy import func, select

from tests.test_message_counting_v31 import _handler, _message
from tests.v30_helpers import FIRST, rules_for, seed_v3
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import GroupActivityRule
from tg_lottery.infrastructure.db import Participant, TicketLedger
from tg_lottery.telegram.creator_group_bonus import (
    apply_action,
    editor_view,
    input_prompt,
    receive_input,
)
from tg_lottery.telegram.draft_config import new_draft

CHAT_ID = -1001
SECOND_CHAT = -1002
REWARD = 3
MESSAGE_TARGET = 5
DAILY_CYCLES = 2


def _draft():
    return new_draft() | {
        "participation_groups": [
            {"id": CHAT_ID, "title": "A群"},
            {"id": SECOND_CHAT, "title": "B群"},
        ],
        "admission": {"type": "message", "groups": {str(CHAT_ID): {"daily_messages": 10}}},
        "group_invite_rules": {str(CHAT_ID): {"enabled": True, "invites_per_cycle": 2}},
        "group_activity_rules": {
            str(CHAT_ID): GroupActivityRule(
                messages_per_cycle=MESSAGE_TARGET, daily_cap_mode="unlimited", daily_max_cycles=None
            ).model_dump()
        },
    }


def test_reward_controls_have_independent_actions_and_units():
    editing = apply_action(_draft(), f"draft_bonus_activity_group:{CHAT_ID}")
    _, rows = editor_view(editing, editing["bonus_working_copy"])
    buttons = dict((action, label) for row in rows for label, action in row)
    assert buttons["draft_bonus_input_reward"] == "自定义 · 1次"
    assert "1次" in buttons["draft_bonus_activity_group_reward_inc"]
    assert buttons["draft_bonus_input_cycle"] == "自定义 · 5条"
    assert "draft_bonus_input_cap" not in buttons
    incremented = apply_action(editing, "draft_bonus_activity_group_reward_inc")
    restored = apply_action(incremented, "draft_bonus_activity_group_reward_dec")
    assert incremented["bonus_working_copy"]["rule"]["reward_tickets"] == 2
    assert restored == editing
    with pytest.raises(UserFacingError, match="正整数"):
        apply_action(editing, "draft_bonus_activity_group_reward_dec")


def test_custom_reward_saves_only_target_field_and_copy_uses_saved_rule():
    original = _draft()
    before = deepcopy(original)
    editing = apply_action(original, f"draft_bonus_activity_group:{CHAT_ID}")
    prompted = apply_action(editing, "draft_bonus_input_reward")
    assert (
        "每轮奖励次数\N{FULLWIDTH LEFT PARENTHESIS}次\N{FULLWIDTH RIGHT PARENTHESIS}"
        in input_prompt(prompted)
    )
    changed = receive_input(prompted, str(REWARD))
    assert changed["group_activity_rules"] == original["group_activity_rules"]
    saved = apply_action(changed, "draft_bonus_activity_save")
    rule = saved["group_activity_rules"][str(CHAT_ID)]
    assert (rule["reward_tickets"], rule["messages_per_cycle"], rule["daily_max_cycles"]) == (
        REWARD,
        MESSAGE_TARGET,
        None,
    )
    assert saved["admission"] == original["admission"]
    assert saved["group_invite_rules"] == original["group_invite_rules"]
    reopened = apply_action(saved, f"draft_bonus_activity_group:{CHAT_ID}")
    copying = apply_action(reopened, "draft_bonus_copy_start")
    copying = apply_action(copying, f"draft_bonus_copy_toggle:{SECOND_CHAT}")
    copied = apply_action(copying, "draft_bonus_copy_confirm")
    assert copied["group_activity_rules"][str(SECOND_CHAT)] == rule
    assert original == before


@pytest.mark.parametrize("text", ["0", "-1", "1.5", "", "不是次数"])
def test_invalid_reward_preserves_other_pending_values_and_cancel_keeps_working_copy(text):
    editing = apply_action(_draft(), f"draft_bonus_activity_group:{CHAT_ID}")
    editing = apply_action(editing, "draft_bonus_preset:cycle:20")
    prompted = apply_action(editing, "draft_bonus_input_reward")
    before = deepcopy(prompted)
    with pytest.raises(UserFacingError, match="正整数"):
        receive_input(prompted, text)
    assert prompted == before
    cancelled = apply_action(prompted, "draft_bonus_input_cancel")
    assert cancelled["bonus_working_copy"] == editing["bonus_working_copy"]
    assert (
        apply_action(cancelled, "draft_bonus_group_cancel")["group_activity_rules"]
        == _draft()["group_activity_rules"]
    )


def test_reward_input_does_not_apply_to_invitation_rules():
    invite = apply_action(_draft(), f"draft_bonus_invite_group:{CHAT_ID}")
    with pytest.raises(UserFacingError, match="字段已失效"):
        receive_input(invite | {"bonus_input_field": "reward"}, str(REWARD))


async def test_custom_reward_is_counted_per_cycle_with_gate_and_message_replay(session_factory):
    rules = rules_for(
        "message", ("activity",), groups={str(FIRST): {"daily_messages": MESSAGE_TARGET}}
    )
    rules = rules.model_copy(
        update={
            "group_activity_rules": {
                str(FIRST): GroupActivityRule(
                    messages_per_cycle=MESSAGE_TARGET,
                    reward_tickets=REWARD,
                    daily_max_cycles=DAILY_CYCLES,
                )
            }
        }
    )
    _, participant_id = await seed_v3(session_factory, rules)
    handler = _handler(session_factory)
    messages = [
        _message(number, text=f"奖励验证{number}")
        for number in range(1, MESSAGE_TARGET * DAILY_CYCLES + 1)
    ]
    for message in [*messages, *messages]:
        await handler._message(message)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        assert participant.admission_qualified
        assert participant.ticket_total_cache == 1 + REWARD * DAILY_CYCLES
        total = await session.scalar(
            select(func.sum(TicketLedger.delta)).where(
                TicketLedger.participant_id == participant_id
            )
        )
        assert total == 1 + REWARD * DAILY_CYCLES
