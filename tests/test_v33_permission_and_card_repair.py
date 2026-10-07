import asyncio
import logging
from types import SimpleNamespace

import pytest

from tests.test_campaign_card_layout import card_payload
from tests.v22_bonus_test_helpers import draft_data
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.presentation.participation_rules import rule_manifest
from tg_lottery.runtime import ContextFormatter
from tg_lottery.telegram.creator_bonus_permissions import verify_publication_bonus_permissions
from tg_lottery.telegram.required_chats import resolve_required_chat


class BarrierBot:
    id = 9001

    def __init__(self, expected=3, *, member_error=None):
        self.expected = expected
        self.calls = []
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        self.member_error = member_error

    async def _wait(self, kind, chat_id):
        self.calls.append((kind, chat_id))
        if len(self.calls) == self.expected:
            self.ready.set()
        await self.release.wait()

    async def get_chat(self, chat_id):
        await self._wait("chat", chat_id)
        return SimpleNamespace(
            id=chat_id,
            title="测试群",
            type="supergroup",
            username="test_group",
            permissions=SimpleNamespace(can_send_messages=True),
        )

    async def get_chat_member(self, chat_id, user_id):
        await self._wait("member", chat_id)
        if self.member_error is not None:
            raise self.member_error
        return SimpleNamespace(status="administrator", can_invite_users=True)


async def finish(task, bot):
    try:
        await asyncio.wait_for(bot.ready.wait(), 1)
    finally:
        bot.release.set()
    return await asyncio.wait_for(task, 1)


async def test_numeric_target_fetches_chat_and_both_members_without_serial_api_wait():
    bot = BarrierBot()
    task = asyncio.create_task(resolve_required_chat(bot, -1001, admin_user_id=201))
    result = await finish(task, bot)
    assert result["creator_admin_validated"] and result["bot_admin_validated"]
    assert len(bot.calls) == 3


async def test_distinct_targets_share_bounded_parallel_permission_checks():
    data = draft_data() | {
        "bonus_types": ["activity"],
        "group_activity_rules": {"-1001": {"enabled": True}, "-1002": {"enabled": True}},
    }
    bot = BarrierBot(expected=6)
    task = asyncio.create_task(verify_publication_bonus_permissions(bot, 201, data))
    await finish(task, bot)
    assert len(bot.calls) == 6
    assert {chat for _, chat in bot.calls} == {-1001, -1002}


async def test_permission_check_preserves_unexpected_error_instead_of_faking_validation():
    bot = BarrierBot(member_error=RuntimeError("transport failed"))
    task = asyncio.create_task(resolve_required_chat(bot, -1001, admin_user_id=201))
    with pytest.raises(RuntimeError, match="transport failed"):
        await finish(task, bot)


async def test_numeric_permission_denial_still_rejects_target():
    bot = BarrierBot()
    original = bot.get_chat_member

    async def non_admin(chat_id, user_id):
        await original(chat_id, user_id)
        return SimpleNamespace(status="member", can_invite_users=False)

    bot.get_chat_member = non_admin
    task = asyncio.create_task(resolve_required_chat(bot, -1001, admin_user_id=201))
    with pytest.raises(UserFacingError, match="管理员"):
        await finish(task, bot)


def test_long_cover_description_keeps_all_conditions_and_prizes_on_main_caption():
    payload = card_payload()
    payload["presentation"]["description"] = "完整说明与兑奖事项" * 70
    payload["prizes"] = [
        {"name": "100优惠券", "winner_count": 5},
        {"name": "200优惠券", "winner_count": 2},
        {"name": "特别礼物", "winner_count": 1},
    ]
    caption, pages = rule_manifest(payload)
    assert pages and html_utf16_length(caption) <= 900
    assert "一群</a>" in caption and "二群</a>" in caption and "公告频道</a>" in caption
    for prize in payload["prizes"]:
        assert f"{prize['name']} \u00d7 <b>{prize['winner_count']}</b>" in caption
    markers = ("📮", "🎁 <b>奖品", "⚠️", "🔋", "📅")
    assert [caption.index(x) for x in markers] == sorted(caption.index(x) for x in markers)
    full = "\n\n".join(pages)
    assert payload["presentation"]["description"] in full
    assert full.index("🎁 <b>奖品") < full.index("⚠️") < full.index("完整统计与加成")


def test_large_target_and_prize_list_keeps_complete_order_in_rule_messages():
    payload = card_payload()
    payload["presentation"]["required_chats"] = [
        {"id": -100900 - i, "title": f"目标群{i}" * 12, "join_url": f"https://t.me/target_{i}"}
        for i in range(20)
    ]
    payload["prizes"] = [{"name": f"奖品{i}" * 15, "winner_count": i + 1} for i in range(20)]
    caption, pages = rule_manifest(payload)
    assert "正文较长" in caption and html_utf16_length(caption) <= 900
    assert "🎁 <b>奖品内容" in caption and payload["prizes"][0]["name"] in caption
    full = "\n\n".join(pages)
    assert all(item["title"] in full for item in payload["presentation"]["required_chats"])
    assert all(item["name"] in full for item in payload["prizes"])
    assert full.index("目标群19") < full.index("🎁 <b>奖品") < full.index("⚠️")


def test_runtime_logs_include_action_processing_and_queue_times_without_payload():
    record = logging.LogRecord("creator", logging.INFO, __file__, 1, "finished", (), None)
    record.action = "draft_gate:save"
    record.elapsed_ms = 123
    record.queue_wait_ms = 45
    record.completed = False
    formatted = ContextFormatter("%(message)s").format(record)
    for expected in (
        "action=draft_gate:save",
        "elapsed_ms=123",
        "queue_wait_ms=45",
        "completed=False",
    ):
        assert expected in formatted
