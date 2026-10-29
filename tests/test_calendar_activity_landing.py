from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.v30_helpers import FIRST, START, rules_for
from tg_lottery.domain.enums import CampaignState
from tg_lottery.presentation.participation_rules import complete_rule_pages, rule_manifest
from tg_lottery.telegram.member_activity_landing import _rules_payload
from tg_lottery.telegram.member_activity_preview import show_activity_preview


def calendar_campaign():
    rules = rules_for("message", groups={str(FIRST): dict(daily_messages=36, consecutive_days=3)})
    return SimpleNamespace(
        id="calendar-campaign",
        public_ref="calendar_ref",
        name="固定日期任务",
        state=CampaignState.RUNNING,
        timezone="Asia/Shanghai",
        preheat_at=START,
        cutoff_at=START + timedelta(days=2),
        draw_at=START + timedelta(days=2, minutes=5),
        rules=rules.model_dump(mode="json") | {"message_task_calendar": "campaign-days-v1"},
        presentation={"participation_groups": [{"id": FIRST, "title": "任务群"}]},
    )


def test_member_rule_payload_contains_saved_calendar_start_for_card_and_rules():
    campaign = calendar_campaign()
    flow = SimpleNamespace(_bot_username="lottery_test_bot")
    payload = _rules_payload(flow, campaign, [])
    assert payload["preheat_at"] == START.isoformat()
    card, _pages = rule_manifest(payload)
    rules = "\n".join(complete_rule_pages(payload))
    assert card and "本期任务时间" in rules
    assert "2026年10月04日" in rules
    assert "每天 36 条" in rules
    assert "join_calendar_ref" in card


def test_publication_payload_also_contains_fixed_calendar_start():
    from tg_lottery.application.publication import campaign_card_payload

    campaign = calendar_campaign()
    payload = campaign_card_payload(campaign, "lottery_test_bot", [])
    assert payload["preheat_at"] == START.isoformat()
    assert "本期任务时间" in "\n".join(complete_rule_pages(payload))


async def test_real_preview_entry_renders_fixed_calendar_without_missing_start():
    campaign = calendar_campaign()
    flow = SimpleNamespace(
        _bot_username="lottery_test_bot",
        _campaign=AsyncMock(return_value=campaign),
        _queries=SimpleNamespace(
            prizes=AsyncMock(return_value=[]),
            operational_issues=AsyncMock(return_value=[]),
            campaign_outcome=AsyncMock(return_value=SimpleNamespace(kind="open")),
        ),
        _rule_rows=lambda *args, **kwargs: [],
        _render=AsyncMock(),
    )
    context = SimpleNamespace(route="m:detail:calendar_ref")
    await show_activity_preview(flow, SimpleNamespace(), context)
    flow._render.assert_awaited_once()
    assert "固定日期任务" in flow._render.await_args.kwargs["pages"][0]
