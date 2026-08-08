from datetime import UTC, datetime, timedelta

import pytest

from tg_lottery.telegram.draft_config import (
    campaign_input,
    missing_fields,
    new_draft,
    panel_text,
    parse_draw_at,
)


def _complete_draft() -> dict[str, object]:
    data = new_draft(chat_id=-1001, group_title="测试群", timezone="Asia/Shanghai")
    data.update(
        {
            "title": "周末福利",
            "draw_at": "2026-08-09T20:00:00+08:00",
            "prizes": [
                {"name": "一等奖", "winner_count": 1},
                {"name": "二等奖", "winner_count": 2},
            ],
        }
    )
    return data


def test_new_draft_only_requires_three_simple_fields() -> None:
    data = new_draft(chat_id=-1001, group_title="测试群", timezone="Asia/Shanghai")

    assert missing_fields(data) == ("抽奖标题", "至少一个奖品", "开奖时间")
    assert "只需设置标题、开奖时间和奖品" in panel_text(data)
    assert "开奖前 5 分钟" in panel_text(data)
    assert "系统自动" in panel_text(data)


def test_single_local_draw_time_builds_internal_timeline_and_rules() -> None:
    data = _complete_draft()
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

    draft, prizes = campaign_input(data, now=now)

    assert draft.preheat_at == now
    assert draft.draw_at == datetime(2026, 8, 9, 20, 0, tzinfo=draft.draw_at.tzinfo)
    assert draft.cutoff_at == draft.draw_at - timedelta(minutes=5)
    assert draft.rules.activity.message_threshold == 10
    assert draft.rules.invite.retention_hours == 24
    assert [prize.winner_count for prize in prizes] == [1, 2]
    assert all(prize.claim_instructions == "请联系活动管理员领取奖品" for prize in prizes)


def test_draw_time_is_parsed_in_group_timezone() -> None:
    parsed = parse_draw_at("2026-08-09 20:00", "Asia/Shanghai")

    assert parsed.isoformat() == "2026-08-09T20:00:00+08:00"


def test_publish_rejects_draw_without_settlement_window() -> None:
    data = _complete_draft()
    data["draw_at"] = "2026-08-09T18:04:00+08:00"
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="至少需要晚于当前时间 5 分钟"):
        campaign_input(data, now=now)
