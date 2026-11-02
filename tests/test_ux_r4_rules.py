from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.test_campaign_card_layout import card_payload
from tests.v30_helpers import FIRST, SECOND, rules_for
from tg_lottery.application.message_calendar_views import calendar_group_lines
from tg_lottery.domain.base_activity import BaseProgress
from tg_lottery.domain.message_calendar import task_date_range, task_dates
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.campaign_card import membership_blocks
from tg_lottery.presentation.gate_rules import gate_rule_text
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.presentation.participation_rules import public_rule_blocks, rule_manifest
from tg_lottery.telegram.creator_behavior_views import admission_summary
from tg_lottery.telegram.creator_bonus_policy import POLICY_NOTES, current_bonus_policy
from tg_lottery.telegram.creator_reuse import reusable_v3_draft, visible_import_notes
from tg_lottery.telegram.creator_views import dashboard_rows, preview_text
from tg_lottery.telegram.draft_config import new_draft


@pytest.mark.parametrize(
    "mode,days,expected",
    [
        ("period", 1, "本期累计有效发言满 66 条"),
        ("daily", 1, "任意 1 天有效发言满 66 条"),
        ("daily", 3, "连续 3 天\uff0c每天有效发言满 66 条"),
    ],
)
def test_speech_gate_text_matches_card_rules_and_creator(mode, days, expected):
    rules = rules_for(
        "message",
        groups={str(FIRST): dict(count_mode=mode, daily_messages=66, consecutive_days=days)},
    )
    payload = card_payload(bonuses=()) | {"rules": rules.model_dump(mode="json")}
    before = deepcopy(payload)
    rule = rules.admission.groups[str(FIRST)]
    assert gate_rule_text(rule) == expected
    card = "\n".join(membership_blocks(payload))
    full = "\n".join(public_rule_blocks(payload))
    assert expected.replace("66", "<b>66</b>") in card
    assert expected in full
    draft = new_draft() | {"admission": payload["rules"]["admission"]}
    assert expected in admission_summary(draft)
    if mode == "period":
        assert "0 点换日" not in full and "自然日统计" not in full
        assert "跨日累加\uff0c不按天清零" in full
    assert "保留本场资格" not in full
    assert payload == before


def test_mixed_modes_keep_daily_explanation_attached_to_daily_group():
    rules = rules_for(
        "message",
        groups={
            str(FIRST): dict(count_mode="period", daily_messages=66),
            str(SECOND): dict(daily_messages=20, consecutive_days=2),
        },
    )
    payload = card_payload(bonuses=()) | {"rules": rules.model_dump(mode="json")}
    full = "\n".join(public_rule_blocks(payload))
    assert "二群每日门槛\uff1a按北京时间自然日计\uff080 点换日\uff09" in full
    assert "一群每日门槛" not in full
    assert full.count("跨日累加\uff0c不按天清零") == 1
    assert full.count("0 点换日") == 1


def test_period_gate_with_daily_bonus_only_mentions_daily_reset_for_bonus():
    rules = rules_for(
        "message", ("activity",), groups={str(FIRST): dict(count_mode="period", daily_messages=66)}
    )
    payload = card_payload() | {"rules": rules.model_dump(mode="json")}
    full = "\n".join(public_rule_blocks(payload))
    assert "每日发言加成按北京时间 0 点换日" in full
    assert "每日门槛\uff1a" not in full


@pytest.mark.parametrize("days", [3, 8])
def test_fixed_calendar_uses_exact_dates_and_preserves_caption_pagination(days):
    rules = rules_for(
        "message", groups={str(FIRST): dict(daily_messages=20, consecutive_days=days)}
    )
    rules = CampaignRules.model_validate(
        rules.model_dump() | {"message_task_calendar": "campaign-days-v1"}
    )
    payload = card_payload(bonuses=()) | {"rules": rules.model_dump(mode="json")}
    start = datetime.fromisoformat("2026-10-29T20:00:00+08:00")
    cutoff = (start + timedelta(days=days - 1)).replace(hour=19, minute=55)
    payload.update(preheat_at=start.isoformat(), cutoff_at=cutoff.isoformat())
    full = "\n".join(public_rule_blocks(payload))
    assert "连续" not in full and "完成一次即可" not in full
    assert "2026年10月29日" in full and "每天各满" in full
    assert "多发不能抵其他日期" in full
    caption, pages = rule_manifest(payload)
    assert html_utf16_length(caption) <= 900
    assert all(html_utf16_length(page) <= 4096 for page in pages)
    payload["presentation"]["description"] = new_draft()["description"] * 5
    long_caption, long_pages = rule_manifest(payload)
    assert html_utf16_length(long_caption) <= 900
    assert long_pages
    campaign = SimpleNamespace(preheat_at=start, cutoff_at=cutoff, timezone="Asia/Shanghai")
    assert task_dates(campaign) == task_date_range(start, cutoff, timezone="Asia/Shanghai")
    assert len(task_dates(campaign)) == days


def test_creator_dashboard_has_one_action_and_permanent_gate():
    for draft in [
        new_draft(),
        new_draft() | {"participation_groups": [{"id": FIRST, "title": "一群"}]},
    ]:
        actions = [action for row in dashboard_rows(draft) for _, action in row]
        assert len(actions) == len(set(actions))
        assert "draft_gate" in actions
        assert "下一项\uff1a" in preview_text(draft)


@pytest.mark.parametrize("note", POLICY_NOTES)
def test_version_notes_are_removed_without_dropping_actual_reuse_notes(note):
    draft = new_draft() | {"import_notes": [note, "请重新设置开奖时间"]}
    assert current_bonus_policy(draft)["import_notes"] == ["请重新设置开奖时间"]
    text = preview_text(draft)
    assert note not in text and "请重新设置开奖时间" in text


@pytest.mark.parametrize(
    "source,values,expected",
    [
        ("chat_message_thresholds", {str(FIRST): 66, str(SECOND): 12}, "累计满 66 条"),
        (
            "base_activity_rules",
            {str(FIRST): dict(daily_messages=20, consecutive_days=3)},
            "连续 3 天\uff0c每天有效发言满 20 条",
        ),
        (
            "base_activity_rules",
            {str(FIRST): dict(count_mode="period", daily_messages=66)},
            "本期累计有效发言满 66 条",
        ),
    ],
)
def test_reuse_notes_preserve_original_gate_mode(source, values, expected):
    data = new_draft() | {source: values, "participation_groups": [{"id": FIRST, "title": "一群"}]}
    reused = reusable_v3_draft(data)
    notes = "\n".join(reused["import_notes"])
    assert expected in notes and "一群" in notes
    assert "{'" not in notes
    saved = data | {"import_notes": ["原发言条件: " + str(values)]}
    assert expected in "\n".join(visible_import_notes(saved))


@pytest.mark.parametrize(
    "offset,messages,expected",
    [(-60, 0, "⏳ 待开始"), (0, 0, "❌ 未完成"), (60, 0, "❌ 未完成"), (60, 20, "✅ 已完成")],
)
def test_calendar_status_uses_start_instant_and_keeps_future_waiting(offset, messages, expected):
    start = datetime.fromisoformat("2026-11-02T20:00:00+08:00")
    campaign = SimpleNamespace(
        preheat_at=start, cutoff_at=start + timedelta(days=2), timezone="Asia/Shanghai"
    )
    rule = SimpleNamespace(daily_messages=20, consecutive_days=3)
    lines = calendar_group_lines(
        campaign,
        rule,
        BaseProgress(messages, int(messages >= 20), False),
        name="一群",
        day=start.date(),
        counts={start.date(): messages},
        now=start + timedelta(seconds=offset),
    )
    assert lines[2].endswith(expected)
    assert lines[-1].endswith("⏳ 待开始")
    assert ("⏳ 待开始" in lines[0]) == (offset < 0)
