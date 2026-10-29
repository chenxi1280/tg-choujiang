# ruff: noqa: RUF001

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_campaign_card_layout import card_payload
from tests.test_v23_bonus_navigation import BonusQueries, _bonus_state
from tests.test_v33_group_reputation import campaign, message
from tests.v30_helpers import rules_for
from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules, GroupActivityRule
from tg_lottery.presentation.campaign_card import bonus_block
from tg_lottery.telegram.gateway import _campaign_keyboard
from tg_lottery.telegram.group_reputation import GroupReputation, _rules
from tg_lottery.telegram.help_views import guide_topic
from tg_lottery.telegram.member_bonus_views import build_bonus_category
from tg_lottery.telegram.member_group_invites import build_group_invite_payload
from tg_lottery.telegram.member_navigation import _rule_status
from tg_lottery.telegram.member_window_guidance import window_guidance
from tg_lottery.telegram.ui_style import plain_label


@pytest.mark.parametrize("key", ["create", "member", "bonus", "results"])
def test_help_topics_do_not_include_the_next_topic(key):
    markers = {
        "create": "➕ 发起抽奖",
        "member": "🛂 参加抽奖",
        "bonus": "📈 邀请与发言抽奖加成",
        "results": "🏆 开奖与领奖",
    }
    text, _keyboard = guide_topic(key, bot_username="lottery_bot")
    assert text.startswith(markers[key])
    assert all(marker not in text for topic, marker in markers.items() if topic != key)


@pytest.mark.parametrize("bonuses", [(), ("activity",), ("invite",), ("activity", "invite")])
def test_card_actions_match_the_frozen_enabled_capabilities(bonuses):
    payload = card_payload(bonuses=bonuses)
    markup = _campaign_keyboard(payload)
    buttons = [button for row in markup.inline_keyboard for button in row]
    routes = {button.url for button in buttons}
    assert ("https://t.me/lottery_test_bot?start=invite_abcdefgh" in routes) == (
        "invite" in bonuses
    )
    assert ("https://t.me/lottery_test_bot?start=bonus_abcdefgh" in routes) == bool(bonuses)
    assert all(button.url and button.callback_data is None for button in buttons)
    assert markup.inline_keyboard[0][0].url == "https://t.me/lottery_test_bot?start=join_abcdefgh"
    assert markup.inline_keyboard[0][0].callback_data is None
    text = bonus_block(payload)
    assert text.count('href="https://t.me/lottery_test_bot?start=rules_abcdefgh"') == 2
    assert ("群内输入" in text) == ("activity" in bonuses)


def test_invite_gate_without_rewards_still_has_invitation_entry():
    payload = card_payload(mode="invite", bonuses=())
    routes = {button.url for row in _campaign_keyboard(payload).inline_keyboard for button in row}
    assert "https://t.me/lottery_test_bot?start=invite_abcdefgh" in routes
    assert "https://t.me/lottery_test_bot?start=bonus_abcdefgh" not in routes


async def test_invitation_entry_filters_activity_only_groups_before_pagination():
    item, progress, statuses = _bonus_state()
    raw = CampaignRules.model_validate(item.rules).model_dump()
    for index in range(8):
        chat_id = -2000 - index
        raw["group_activity_rules"][str(chat_id)] = GroupActivityRule().model_dump()
        item.presentation["participation_groups"].append(
            {"id": chat_id, "title": f"仅活跃群{index}"}
        )
    item.rules = CampaignRules.model_validate(raw).model_dump()
    text, keyboard = await build_group_invite_payload(
        item,
        user_id=201,
        page=1,
        queries=BonusQueries(progress, statuses),
        cipher=SimpleNamespace(decrypt=lambda *_args, **_kwargs: "https://t.me/+test-link"),
        bot_username="lottery_bot",
    )
    assert "邀请抽奖加成入口" in text and "入群满 1 小时" in text
    assert "仅活跃群" not in text and "今日发言" not in text
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert not any(button.callback_data == "member:noop" for button in buttons)
    assert next(
        button for button in buttons if plain_label(button.text) == "刷新邀请状态"
    ).callback_data == (f"member:invite_page:{item.public_ref}:0")


@pytest.mark.parametrize(
    ("state", "base", "expected"),
    [
        (QualificationState.ELIGIBLE, True, "8"),
        (QualificationState.ELIGIBLE, False, "0"),
        (QualificationState.UNKNOWN, True, "待核查"),
        (QualificationState.INELIGIBLE, True, "0"),
    ],
)
async def test_reputation_query_distinguishes_all_group_rewards_from_effective_tickets(
    state, base, expected
):
    item = campaign()
    item.rules = rules_for("message", ("activity", "invite")).model_dump()
    detail = SimpleNamespace(
        activity=5, registration=1, invitation=2, total=8, qualification=state, base_qualified=base
    )
    progress = SimpleNamespace(
        activities=(
            SimpleNamespace(
                chat_id=-100123, valid_messages=1, total_awarded_tickets=2, awarded_tickets=0
            ),
        )
    )
    queries = SimpleNamespace(
        active_campaigns_by_chat=AsyncMock(return_value=[item]),
        participant_campaign_detail=AsyncMock(return_value=detail),
        group_bonus_progress=AsyncMock(return_value=progress),
    )
    incoming = message()
    await GroupReputation(queries=queries, bot_username="lottery_bot").message(incoming, "人品")
    text = incoming.answer.call_args.args[0]
    assert "本群发言抽奖加成累计已入账：2" in text
    assert "本场发言抽奖加成累计（所有群）：5" in text
    assert f"当前有效次数：{expected}" in text
    assert "基础：1 次；邀请抽奖加成：2 次（不计入发言抽奖加成排行）" in text
    assert ("加成暂存" in text) == (not base)


def test_legacy_reputation_rules_keep_actual_global_scope_and_reward_limits():
    item = campaign()
    rules = CampaignRules(
        bonus_types=("activity", "invite"),
        activity={"message_threshold": 7, "reward_tickets": 3, "daily_ticket_cap": 6},
        invite={"reward_tickets": 4, "retention_hours": 48},
    )
    item.cutoff_at = datetime.now(UTC) + timedelta(days=3)
    text = _rules(item, rules, -100123)
    assert "每个统计群每天有效发言满 7 条" in text
    assert "全部达标 +3 次" in text and "每日最多 6 次" in text
    assert "每个有效邀请 +4 次" in text and "入群满48小时" in text
    assert "未开启" not in text


@pytest.mark.parametrize(
    ("state", "outcome", "label"),
    [
        (CampaignState.CANCELLED, "cancelled", "已取消"),
        (CampaignState.ARCHIVED, "drawn", "已开奖"),
        (CampaignState.ARCHIVED, "unconfirmed", "历史结果未确认"),
    ],
)
def test_closed_rule_page_labels_original_dates_and_never_prompts_new_qualification(
    state, outcome, label
):
    item = campaign(state=state)
    original = item.cutoff_at
    text = _rule_status(item, outcome=CampaignOutcomeFacts(outcome))
    assert label in text and "本场原计划" in text and "不再开放报名" in text
    assert window_guidance(item, CampaignRules.model_validate(item.rules)) == ""
    assert item.cutoff_at == original


@pytest.mark.parametrize(
    "state", [CampaignState.RUNNING, CampaignState.DRAWN, CampaignState.CANCELLED]
)
async def test_activity_only_bonus_progress_is_paginated_by_six_groups(state):
    campaign, progress = _activity_only_state(state)
    common = dict(
        user_id=201,
        queries=BonusQueries(progress, {}),
        cipher=object(),
        bot_username="test_bot",
        unavailable={},
    )
    first, _ = await build_bonus_category(campaign, category="activity", page=0, **common)
    second, _ = await build_bonus_category(campaign, category="activity", page=1, **common)
    assert "群0" in first and "群5" in first and "群6" not in first
    assert "群6" in second and "群0" not in second
    if state is not CampaignState.RUNNING:
        assert "距下一次还差" not in first + second
        assert "累计达标 6 天、获得 6 次" in second


def _activity_only_state(state):
    now = datetime.now(UTC)
    chat_ids = tuple(-1000 - index for index in range(7))
    rules = CampaignRules(
        bonus_types=("activity",),
        group_activity_rules={str(chat_id): GroupActivityRule() for chat_id in chat_ids},
    )
    campaign = SimpleNamespace(
        id="campaign-1",
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        name="七群活动",
        state=state,
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=1),
        rules=rules.model_dump(mode="json"),
        draw_at=now + timedelta(hours=2),
        timezone="Asia/Shanghai",
        presentation={
            "participation_groups": [
                {"id": chat_id, "title": f"群{index}"} for index, chat_id in enumerate(chat_ids)
            ]
        },
    )
    progress = SimpleNamespace(
        base_progress="",
        invitations=(),
        activities=tuple(
            SimpleNamespace(
                chat_id=chat_id,
                valid_messages=index,
                awarded_tickets=0,
                total_awarded_tickets=index,
                qualified_days=index,
            )
            for index, chat_id in enumerate(chat_ids)
        ),
    )
    return campaign, progress


async def test_invitation_permission_failure_is_visible_when_activity_also_enabled():
    item, progress, statuses = _bonus_state()
    text, keyboard = await build_group_invite_payload(
        item,
        user_id=201,
        page=0,
        queries=BonusQueries(progress, statuses),
        cipher=object(),
        bot_username="test_bot",
        unavailable={-1001: "机器人权限待修复"},
    )
    assert "A群：⚠ 机器人权限待修复" in text
    assert not any(button.copy_text for row in keyboard.inline_keyboard for button in row)
    refresh = keyboard.inline_keyboard[0][0]
    assert refresh.callback_data == f"member:invite:{item.public_ref}"


@pytest.mark.parametrize("rewards", [True, False])
async def test_cancelled_invitation_entry_has_history_action_and_one_cancellation_notice(rewards):
    item, progress, statuses = _bonus_state()
    item.state = CampaignState.CANCELLED
    if not rewards:
        item.rules = CampaignRules(
            participation_rules_version="v3.0",
            admission={"type": "invite", "groups": {"-1001": {"required_valid_invites": 1}}},
        ).model_dump()
    text, keyboard = await build_group_invite_payload(
        item,
        user_id=201,
        page=0,
        queries=BonusQueries(progress, statuses, outcome=CampaignOutcomeFacts("cancelled")),
        cipher=object(),
        bot_username="test_bot",
    )
    assert text.count("本场已取消") == 1
    assert "人数继续累计" not in text and "距下一次" not in text
    assert not any(
        button.copy_text or button.url for row in keyboard.inline_keyboard for button in row
    )
    assert any(
        button.callback_data == f"member:invites:{item.public_ref}:0"
        for row in keyboard.inline_keyboard
        for button in row
    )
