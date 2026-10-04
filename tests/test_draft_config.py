from datetime import UTC, datetime, timedelta

import pytest

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.draft_config import (
    campaign_input,
    missing_fields,
    new_draft,
    parse_draw_at,
    rules_from_data,
)


def _complete_draft() -> dict[str, object]:
    data = new_draft(chat_id=-1001, group_title="测试群", timezone="Asia/Shanghai")
    data.update(
        {
            "participation_rules_version": None,
            "invite_cap_mode": "limited",
            "invite_bonus_cap": 3,
            "title": "周末福利",
            "draw_at": "2026-08-09T20:00:00+08:00",
            "prizes": [
                {"name": "一等奖", "winner_count": 1},
                {"name": "二等奖", "winner_count": 2},
            ],
        }
    )
    return data


def test_new_draft_requires_title_prize_time_and_admission() -> None:
    data = new_draft(chat_id=-1001, group_title="测试群", timezone="Asia/Shanghai")

    fields = missing_fields(data)
    assert fields[:3] == ("抽奖标题", "至少一个奖品", "开奖时间")
    assert len(fields) == 4 and fields[3].startswith("抽奖门槛")


def test_new_draft_does_not_delete_completion_messages_by_default() -> None:
    data = new_draft()

    assert data["delete_trigger_message"] is False
    assert data["completion_delete_after_seconds"] is None


def test_new_draft_starts_with_empty_chat_message_thresholds() -> None:
    assert new_draft()["chat_message_thresholds"] == {}


def test_rules_from_data_includes_chat_message_thresholds() -> None:
    data = {
        "timezone": "Asia/Shanghai",
        "invite_preset": "standard",
        "chat_message_thresholds": {"-100123": 10, "-100456": None},
    }

    rules = rules_from_data(data)

    assert rules.message_thresholds == {"-100123": 10}


def test_campaign_input_freezes_chat_message_thresholds() -> None:
    data = _complete_draft()
    data["chat_message_thresholds"] = {"-100123": 10}
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

    draft, _ = campaign_input(data, now=now)

    assert draft.rules.message_thresholds == {"-100123": 10}
    assert draft.presentation["chat_message_thresholds"] == {"-100123": 10}


def test_single_local_draw_time_builds_internal_timeline_and_rules() -> None:
    data = _complete_draft()
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

    draft, prizes = campaign_input(data, now=now)

    assert draft.preheat_at == now
    assert draft.draw_at == datetime(2026, 8, 9, 20, 0, tzinfo=draft.draw_at.tzinfo)
    assert draft.cutoff_at == draft.draw_at - timedelta(minutes=5)
    assert draft.rules.activity.message_threshold == 10
    assert draft.rules.activity.settle_once_per_day is True
    assert draft.rules.invite.retention_hours == 24
    assert draft.rules.invite.mode == "ratio"
    assert draft.rules.invite.valid_invites_per_ticket == 2
    assert draft.rules.invite.max_bonus_tickets == 3
    assert draft.rules.invite.reward_tiers == ()
    assert draft.rules.bonus_types == ()
    assert [prize.winner_count for prize in prizes] == [1, 2]
    assert all(prize.claim_instructions == "请联系活动管理员领取奖品" for prize in prizes)


def test_draw_time_is_parsed_in_group_timezone() -> None:
    parsed = parse_draw_at("2026-08-09 20:00", "Asia/Shanghai")

    assert parsed.isoformat() == "2026-08-09T20:00:00+08:00"


def test_publish_rejects_draw_without_settlement_window() -> None:
    data = _complete_draft()
    data["draw_at"] = "2026-08-09T18:04:00+08:00"
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

    with pytest.raises(UserFacingError, match="至少需要晚于当前时间 5 分钟"):
        campaign_input(data, now=now)


def test_full_draw_freezes_fallback_rules_and_presentation() -> None:
    data = _complete_draft()
    data.update(
        {
            "draw_at": "",
            "draw_mode": "full",
            "participant_target": 88,
            "cover_file_id": "telegram-file-id",
            "description": "满员后进入安全结算",
            "username_required": True,
            "allowed_regions": ["河南", "北京"],
            "required_chat_ids": [-1002, -1003],
            "issuer_name": "发奖专员",
            "luck_mode": "off",
            "message_tips": False,
        }
    )
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

    draft, _ = campaign_input(data, now=now)

    assert draft.draw_at.isoformat() == "2026-09-08T12:00:00+08:00"
    assert draft.rules.participant_target == 88
    assert draft.rules.allowed_regions == ("河南", "北京")
    assert draft.rules.required_chat_ids == (-1002, -1003)
    assert draft.rules.luck_mode == "off"
    assert draft.presentation["cover_file_id"] == "telegram-file-id"
    assert draft.presentation["message_tips"] is False


def test_full_draw_fallback_uses_local_noon_across_dst_change() -> None:
    data = _complete_draft()
    data.update(
        {
            "timezone": "America/New_York",
            "draw_at": "",
            "draw_mode": "full",
            "participant_target": 8,
        }
    )

    draft, _ = campaign_input(data, now=datetime(2026, 10, 20, 16, tzinfo=UTC))

    assert draft.draw_at.isoformat() == "2026-11-19T12:00:00-05:00"
    assert draft.cutoff_at == draft.draw_at - timedelta(minutes=5)
