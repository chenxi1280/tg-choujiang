from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tests.test_campaign_card_layout import card_payload
from tests.v30_helpers import FIRST, rules_for
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.campaign_card import bonus_block, card_blocks
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.presentation.participation_rules import (
    complete_rule_pages,
    public_rule_blocks,
    rule_manifest,
)
from tg_lottery.telegram.bonus_rule_text import global_bonus_lines, per_group_bonus_lines
from tg_lottery.telegram.creator_group_availability import GroupAvailability
from tg_lottery.telegram.creator_renderer import _group_button_text, _group_picker_lines
from tg_lottery.telegram.creator_rule_review import rule_review_pages
from tg_lottery.telegram.creator_sections import section_view
from tg_lottery.telegram.creator_views import description_summary, preview_text
from tg_lottery.telegram.draft_config import new_draft, parse_draw_at
from tg_lottery.telegram.public_projection import format_public_time


@pytest.mark.parametrize("sources", [["platform"], ["platform", "creator"], None])
def test_frozen_platform_targets_are_only_extra_when_creator_also_requires_them(sources):
    data = new_draft() | {
        "required_chats": [
            {"id": -1001, "title": "测试频道", "type": "channel", "sources": sources}
        ],
        "required_chat_ids": [-1001],
    }
    before = deepcopy(data)
    text = preview_text(data)
    if sources == ["platform"]:
        assert "额外参与条件\uff1a无额外条件" in text
    else:
        assert "关注「测试频道」" in text
    assert data == before
    limits, _ = section_view(data, "user_limits")
    assert "允许地区\uff1a不限" in limits


@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai", "Europe/London"])
def test_public_time_preserves_instant_and_uses_beijing_minutes(zone):
    moment = datetime(2026, 12, 31, 16, 4, 59, tzinfo=UTC)
    before = moment.timestamp()
    assert format_public_time(moment, zone) == "2027年1月1日 00:04\uff08北京时间\uff09"
    assert moment.timestamp() == before
    parsed = parse_draw_at("2027-01-01 00:04", "Asia/Shanghai")
    assert parsed == moment.replace(second=0)


@pytest.mark.parametrize("calendar", [False, True])
def test_each_public_message_labels_timezone_once(calendar):
    payload = card_payload(bonuses=())
    if calendar:
        payload["rules"]["message_task_calendar"] = "campaign-days-v1"
    before = deepcopy(payload)
    card = "\n".join(card_blocks(payload))
    assert card.count("\uff08北京时间\uff09") == 1
    for page in complete_rule_pages(payload):
        assert page.count("\uff08北京时间\uff09") == 1
        assert html_utf16_length(page) <= 4096
    assert "秒" not in card and "20:00:01" not in card
    assert payload == before


@pytest.mark.parametrize("double", [True, False])
def test_daily_summary_keeps_all_groups_gate_and_first_day_conditions(double):
    rules = rules_for("message", ("activity",))
    values = rules.model_dump() | {
        "bonus_policy_version": "daily-after-start-v1",
        "activity_bonus_double_count": double,
    }
    values["invite"]["retention_hours"] = 24
    values["group_activity_rules"][str(FIRST)].update(
        daily_cap_mode="limited", daily_max_cycles=1, messages_per_cycle=36
    )
    payload = card_payload() | {"rules": CampaignRules.model_validate(values).model_dump()}
    before = deepcopy(payload)
    summary = bonus_block(payload)
    for clause in [
        "首日不发",
        "次日起",
        "所有奖励群",
        "相关门槛",
        "当天加成发言要求",
        "每天最多1张",
    ]:
        assert clause in summary
    assert "36" not in summary and summary.count(">完整规则</a>") == 1
    full = "\n".join(public_rule_blocks(payload))
    assert "每天36条" in full
    assert ("不双算" in full) == (not double)
    assert payload == before


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"activity": {"reward_tickets": 3, "settle_once_per_day": True}}, "全部达标 +3 张"),
        ({"activity": {"daily_ticket_cap": 5}}, "每日最多 5 张"),
        ({"invite": {"mode": "ratio", "valid_invites_per_ticket": 3}}, "每邀请 3 个"),
        (
            {
                "invite": {
                    "reward_tiers": [{"invite_count": 2, "total_bonus": 5}],
                    "max_bonus_tickets": 5,
                }
            },
            "邀请 2 人\uff0c累计 +5 张",
        ),
        ({"invite": {"reward_tickets": 4}}, "每个有效邀请 +4 张"),
        ({"group_invite_rules": {str(FIRST): {"invites_per_cycle": 7}}}, "每邀请 7 个"),
        (
            {
                "group_activity_rules": {
                    str(FIRST): {"daily_cap_mode": "unlimited", "daily_max_cycles": None}
                }
            },
            "每日不设上限",
        ),
    ],
)
def test_legacy_card_uses_same_clauses_as_complete_rules(values, expected):
    rules = CampaignRules.model_validate({"bonus_types": ["invite", "activity"]} | values)
    payload = card_payload() | {"rules": rules.model_dump(mode="json")}
    before = deepcopy(payload)
    summary = bonus_block(payload)
    complete = "\n".join(public_rule_blocks(payload))
    assert expected in summary and expected in complete
    clauses = per_group_bonus_lines(rules, payload["presentation"]["participation_groups"])
    if not rules.group_invite_rules and not rules.group_activity_rules:
        assert all(clause in summary for clause in global_bonus_lines(rules))
    assert clauses and summary.count(">完整规则</a>") == 1
    assert payload == before


def test_description_is_abbreviated_only_on_dashboard():
    data = new_draft() | {
        "description": "第一行\n第二行\n第三行",
        "admission": rules_for("message").admission.model_dump(),
        "bonus_types": [],
    }
    assert description_summary(data) == "第一行\uff08共 3 行\uff09"
    assert "第二行" not in preview_text(data)
    assert "第一行\n第二行\n第三行" in "".join(rule_review_pages(data))
    assert "地区限制\uff1a不限" in preview_text(data)
    assert data["description"] == "第一行\n第二行\n第三行"


def test_group_counts_and_duplicate_ids_are_visible_in_body_only():
    groups = [
        SimpleNamespace(chat_id=-100123401, title="同名群"),
        SimpleNamespace(chat_id=-100123402, title="同名群"),
        SimpleNamespace(chat_id=-100123403, title="唯一群"),
    ]
    counts = {groups[0].chat_id: 3}
    lines = _group_picker_lines(groups, counts, start=1, duplicates={"同名群"})
    assert "3401" in lines[0] and "3402" in lines[1] and "3403" not in lines[2]
    assert "另有 3 个进行中活动" in lines[0]
    status = GroupAvailability(True, "可选择", "")
    selected = _group_button_text(groups[0], status, True)
    unselected = _group_button_text(groups[1], status, False)
    assert selected.startswith("✅") and unselected.startswith("⬜")
    assert "另有" not in selected and "3401" not in selected


def test_overflow_calendar_summary_keeps_timezone_and_complete_dates():
    payload = card_payload(bonuses=())
    payload["rules"]["message_task_calendar"] = "campaign-days-v1"
    payload["presentation"]["description"] *= 200
    payload["prizes"] = [{"name": "长奖品" * 20, "winner_count": 1} for _ in range(10)]
    card, pages = rule_manifest(payload)
    assert html_utf16_length(card) <= 900 and pages
    assert card.count("\uff08北京时间\uff09") == 1
    assert "2026年10月8日" in "".join(pages)
