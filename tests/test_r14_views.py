from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember

from tests.test_draft_config import _complete_draft
from tests.test_v32_member_navigation import _detail
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_messages import MESSAGE_DELAYS, message_settings_rows
from tg_lottery.telegram.draft_config import campaign_input, ensure_draft_identity, new_draft
from tg_lottery.telegram.lottery_query_views import personal_progress
from tg_lottery.telegram.member_condition_check import target_conditions


def test_new_description_is_editable_clearable_and_old_draft_is_not_backfilled():
    data = new_draft()
    assert "7天" in data["description"] and "1." in data["description"]
    assert (
        ensure_draft_identity(data | {"description": "主办方自定义"})["description"]
        == "主办方自定义"
    )
    assert ensure_draft_identity(data | {"description": ""})["description"] == ""
    assert ensure_draft_identity({"draft_id": "old"})["description"] == ""


def test_future_start_keeps_original_draw_time_and_default_is_immediate():
    now = datetime(2026, 8, 9, 10, tzinfo=UTC)
    data = _complete_draft()
    ordinary, _ = campaign_input(data, now=now)
    scheduled, _ = campaign_input(
        data | {"start_at": (now + timedelta(hours=1)).isoformat()}, now=now
    )
    assert ordinary.preheat_at == now
    assert scheduled.preheat_at == now + timedelta(hours=1)
    assert scheduled.draw_at == ordinary.draw_at and scheduled.cutoff_at == ordinary.cutoff_at


@pytest.mark.parametrize("offset", [-1, 7200])
def test_invalid_start_is_rejected(offset):
    now = datetime(2026, 8, 9, 10, tzinfo=UTC)
    with pytest.raises(UserFacingError):
        campaign_input(
            _complete_draft() | {"start_at": (now + timedelta(seconds=offset)).isoformat()}, now=now
        )


def test_retention_common_options_compact_and_old_durations_retained():
    rows = message_settings_rows(new_draft())
    actions = {action for row in rows for _, action in row}
    assert {
        "draft_messages_delay_15s",
        "draft_messages_delay_20s",
        "draft_messages_more",
    } <= actions
    assert "draft_messages_delay_60m" not in actions
    assert MESSAGE_DELAYS["60m"] == 3600 and MESSAGE_DELAYS["10s"] == 10
    assert all(len(row) <= 2 for row in rows)


async def test_conditions_keep_total_and_hide_only_confirmed_joined_targets():
    targets = [
        SimpleNamespace(
            title=f"频道{n}",
            chat_id=-1000 - n,
            platform=False,
            public_join_url=f"https://t.me/channel{n}",
        )
        for n in range(8)
    ]

    bot = SimpleNamespace(get_chat_member=_observed_member)
    lines, rows = await target_conditions(
        bot, 201, targets=targets, cipher=object(), reference="ref", pending_chats=(-1001,)
    )
    text = "\n".join(lines)
    assert "1/8" in text and "✅" in text and "❌" in text and "⏳ 等待管理员批准" in text
    assert "暂时无法核验" in text
    urls = [button.url for row in rows for button in row if button.url]
    assert "https://t.me/channel0" not in urls and "https://t.me/channel1" in urls
    assert len(urls) == 5
    lines, _ = await target_conditions(
        bot, 201, targets=targets, cipher=object(), reference="ref", page=99
    )
    assert "第 2/2 页" in lines and "1/8" in lines[0]


async def test_personal_summary_keeps_all_three_sections_when_gate_text_is_long():
    from dataclasses import replace

    detail = replace(_detail(), base_progress="逐群门槛进度\n" * 800)
    campaign = SimpleNamespace(
        id="test",
        public_ref="ref",
        name="活动",
        rules={"bonus_types": []},
        state=detail.state,
        preheat_at=datetime.now(UTC) - timedelta(hours=1),
        cutoff_at=datetime.now(UTC) + timedelta(hours=1),
    )
    queries = SimpleNamespace(
        participant_campaign_detail=AsyncMock(return_value=detail),
        campaign_invitation_records=AsyncMock(return_value=None),
    )
    pages = await personal_progress(queries, campaign, user_id=201)
    assert len(pages) > 1
    assert all(word in pages[0] for word in ("门槛进度", "加成进度", "邀请进度"))
    assert "逐群门槛进度" in "\n".join(pages[1:])


async def _observed_member(chat, user):
    if chat == -1002:
        raise TelegramBadRequest(
            method=GetChatMember(chat_id=chat, user_id=user), message="offline"
        )
    return SimpleNamespace(status="member" if chat == -1000 else "left")


def test_detail_times_keep_seconds_and_the_same_saved_instant():
    from dataclasses import replace

    from tg_lottery.telegram.member_navigation_views import participant_detail_view

    moment = datetime(2026, 10, 30, 3, 4, 59, tzinfo=UTC)
    detail = replace(_detail(), draw_at=moment, cutoff_at=moment - timedelta(minutes=5))
    pages, _ = participant_detail_view(detail, user_id=201, bot_username="testbot")
    text = "\n".join(pages)
    assert "2026年10月30日 11:04:59" in text
    assert "2026年10月30日 10:59:59" in text
    assert detail.draw_at == moment
