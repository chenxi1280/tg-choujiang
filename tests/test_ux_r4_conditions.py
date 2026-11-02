from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_lottery.domain.enums import QualificationState
from tg_lottery.presentation.condition_progress import condition_label
from tg_lottery.telegram.member_condition_check import target_conditions
from tg_lottery.telegram.member_missing_targets import build_missing_targets_reply
from tg_lottery.telegram.member_platform_feedback import platform_feedback
from tg_lottery.telegram.platform_condition_view import platform_condition_lines


def _target(n):
    return SimpleNamespace(
        chat_id=-1000 - n,
        title=f"群{n}",
        chat_type="supergroup",
        kind="participation_group",
        platform=False,
        public_join_url=f"https://t.me/test_group{n}",
        encrypted_join_url=None,
        revoked_at=None,
    )


def _campaign():
    return SimpleNamespace(
        public_ref="ux_r4",
        name="测试活动",
        rules={},
        cutoff_at=datetime.now(UTC) + timedelta(days=1),
    )


def test_failure_page_lists_completed_pending_unknown_and_missing():
    targets = tuple(_target(n) for n in range(4))
    observations = (
        {"chat_id": targets[0].chat_id, "state": "eligible"},
        {"chat_id": targets[1].chat_id, "state": "ineligible"},
        {
            "chat_id": targets[2].chat_id,
            "state": "ineligible",
            "observed_status": "join_request_pending",
        },
    )
    text, keyboard = build_missing_targets_reply(
        _campaign(),
        QualificationState.INELIGIBLE,
        targets,
        observations=observations,
        page=1,
        target_url=lambda campaign, target: target.public_join_url,
    )
    assert "入群/关注条件\uff1a已完成 1/4 项\uff08刚刚检查\uff09" in text
    assert "✅ 已完成 · 加入「群0」" in text
    assert "❌ 未完成 · 加入「群1」" in text
    assert "⏳ 等待管理员批准 · 加入「群2」" in text
    assert "⏳ 暂时无法检查 · 加入「群3」" in text
    assert [button.url for row in keyboard.inline_keyboard for button in row if button.url] == [
        targets[1].public_join_url
    ]


def test_all_conditions_are_paginated_even_when_first_page_is_complete():
    targets = tuple(_target(n) for n in range(8))
    observations = tuple(
        {"chat_id": target.chat_id, "state": "eligible" if n < 6 else "ineligible"}
        for n, target in enumerate(targets)
    )
    first, keyboard = build_missing_targets_reply(
        _campaign(),
        QualificationState.INELIGIBLE,
        targets,
        observations=observations,
        page=1,
        target_url=lambda campaign, target: target.public_join_url,
    )
    second, _ = build_missing_targets_reply(
        _campaign(),
        QualificationState.INELIGIBLE,
        targets,
        observations=observations,
        page=2,
        target_url=lambda campaign, target: target.public_join_url,
    )
    assert "6/8" in first and "群5" in first and "群6" not in first
    assert "群6" in second and "群0" not in second
    actions = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "member:missing:ux_r4:2" in actions
    assert len(keyboard.inline_keyboard) == 2


@pytest.mark.parametrize(
    "status,state,label",
    [
        ("bot_started", "eligible", "✅ 已启动机器人"),
        ("bot_not_started", "ineligible", "❌ 尚未启动机器人"),
        ("bot_blocked", "ineligible", "❌ 已屏蔽机器人\uff0c请解除屏蔽后重新进入"),
        (
            "bot_relationship_unknown",
            "unknown",
            "⏳ 暂时无法确认机器人启动状态\uff0c请重新进入检查",
        ),
    ],
)
async def test_bot_state_matches_both_entry_points_without_changing_counter(status, state, label):
    relationships = SimpleNamespace(
        observation=AsyncMock(return_value={"observed_status": status, "state": state})
    )
    campaign = _campaign()
    campaign.rules = {"platform_policy": {"enabled": True}}
    lines, rows = await platform_condition_lines(
        relationships, campaign=campaign, user_id=201, bot_username="example_bot"
    )
    assert lines == [label]
    flow = SimpleNamespace(_relationships=relationships, _bot_username="example_bot")
    from aiogram.types import InlineKeyboardMarkup

    text, keyboard = await platform_feedback(
        flow,
        campaign,
        user_id=201,
        text="入群/关注条件\uff1a已完成 3/3 项",
        keyboard=InlineKeyboardMarkup(inline_keyboard=[]),
    )
    assert text == label + "\n入群/关注条件\uff1a已完成 3/3 项"
    assert bool(rows) == bool(keyboard.inline_keyboard) == (state != "eligible")


async def test_live_check_uses_shared_line_and_does_not_offer_pending_or_unknown_join():
    targets = [_target(n) for n in range(3)]
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status="member"),
                SimpleNamespace(status="left"),
                SimpleNamespace(status="left"),
            ]
        )
    )
    lines, rows = await target_conditions(
        bot,
        201,
        targets=targets,
        cipher=object(),
        reference="ux_r4",
        pending_chats=(targets[1].chat_id,),
    )
    assert lines[0] == "入群/关注条件\uff1a已完成 1/3 项\uff08刚刚检查\uff09"
    assert lines[1] == "✅ 已完成 · 加入「群0」"
    assert lines[2] == "⏳ 等待管理员批准 · 加入「群1」"
    assert [button.url for row in rows for button in row] == [targets[2].public_join_url]
    assert condition_label({"state": "unknown"}) == "⏳ 暂时无法检查"
