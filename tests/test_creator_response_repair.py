import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.v22_bonus_test_helpers import draft_data
from tg_lottery.application.admin_sessions import DraftPanelContext
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as gate
from tg_lottery.telegram import creator_behavior_views as gate_views
from tg_lottery.telegram.creator_bonus_permissions import verify_publication_bonus_permissions
from tg_lottery.telegram.creator_group_availability import inspect_group_availability
from tg_lottery.telegram.creator_group_bonus import apply_action, editor_view, receive_input
from tg_lottery.telegram.creator_group_picker import CreatorGroupPicker
from tg_lottery.telegram.creator_renderer import CreatorRenderer, PanelTarget
from tg_lottery.telegram.required_chats import resolve_required_chat

WAIT_SECONDS = 1
USER_ID = 201
BOT_ID = 9001
CHAT_ID = -1001
PAGE_CHAT_IDS = tuple(range(-1006, -1000))


def _gate_draft():
    data = draft_data() | {"participation_rules_version": "v3.0"}
    data = gate.apply_action(gate.open_editor(data), "draft_gate:mode:message")
    return gate.apply_action(data, f"draft_gate:group:{CHAT_ID}")


@pytest.mark.parametrize("value", [5, 10, 20, 2, 34])
def test_gate_and_bonus_presets_and_custom_values_are_independent(value):
    data = _gate_draft()
    before = deepcopy(data)
    _, rows = gate_views.view(data)
    presets = [action for row in rows for _, action in row if ":quick:daily_messages:" in action]
    assert presets == [f"draft_gate:quick:daily_messages:{count}" for count in (5, 8, 10)]
    assert data[gate.EDITOR_KEY]["rule"]["daily_messages"] == 5
    editing = gate.apply_action(data, "draft_gate:input:daily_messages")
    changed = gate.receive(editing, str(value))
    assert changed[gate.EDITOR_KEY]["rule"]["daily_messages"] == value
    assert changed[gate.EDITOR_KEY]["rule"]["consecutive_days"] == 1
    assert data == before
    bonus = apply_action(data, f"draft_bonus_activity_group:{CHAT_ID}")
    assert bonus["bonus_working_copy"]["rule"]["messages_per_cycle"] == 5
    _, rows = editor_view(bonus, bonus["bonus_working_copy"])
    presets = [action for row in rows for _, action in row if "preset:cycle:" in action]
    assert presets == [f"draft_bonus_preset:cycle:{count}" for count in (5, 8, 10)]
    for count in (5, 8, 10):
        preset = apply_action(bonus, f"draft_bonus_preset:cycle:{count}")
        assert preset["bonus_working_copy"]["rule"]["messages_per_cycle"] == count
        preset_gate = gate.apply_action(data, f"draft_gate:preset:daily_messages:{count}")
        assert preset_gate[gate.EDITOR_KEY]["rule"]["daily_messages"] == count
    custom = receive_input(apply_action(bonus, "draft_bonus_input_cycle"), str(value))
    rule = custom["bonus_working_copy"]["rule"]
    assert rule["messages_per_cycle"] == value
    assert rule["daily_max_cycles"] == bonus["bonus_working_copy"]["rule"]["daily_max_cycles"]
    assert custom[gate.EDITOR_KEY] == data[gate.EDITOR_KEY]


@pytest.mark.parametrize("value", [2, 4, 34])
def test_existing_gate_and_bonus_values_remain_on_reopen(value):
    data = draft_data() | {
        "admission": {"type": "message", "groups": {str(CHAT_ID): {"daily_messages": value}}},
        "group_activity_rules": {str(CHAT_ID): {"enabled": True, "messages_per_cycle": value}},
    }
    reopened = gate.apply_action(gate.open_editor(data), f"draft_gate:group:{CHAT_ID}")
    bonus = apply_action(data, f"draft_bonus_activity_group:{CHAT_ID}")
    assert reopened[gate.EDITOR_KEY]["rule"]["daily_messages"] == value
    assert bonus["bonus_working_copy"]["rule"]["messages_per_cycle"] == value


class ConcurrentBot:
    def __init__(self, expected):
        self.id = BOT_ID
        self.expected = expected
        self.calls = []
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        self.get_chat = AsyncMock(return_value=_chat())

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        if len(self.calls) == self.expected:
            self.ready.set()
        await self.release.wait()
        return SimpleNamespace(status="administrator", can_invite_users=True)


def _chat(*, can_send=True):
    return SimpleNamespace(
        id=CHAT_ID,
        title="测试群",
        type="supergroup",
        username="test_group",
        permissions=SimpleNamespace(can_send_messages=can_send),
    )


async def _finish_concurrent(task, bot):
    try:
        await asyncio.wait_for(bot.ready.wait(), WAIT_SECONDS)
    finally:
        bot.release.set()
    return await asyncio.wait_for(task, WAIT_SECONDS)


@pytest.mark.parametrize("resolver", [inspect_group_availability, resolve_required_chat])
async def test_group_member_checks_start_together(resolver):
    bot = ConcurrentBot(expected=2)
    kwargs = {"admin_user_id": USER_ID} if resolver is resolve_required_chat else {}
    args = () if kwargs else (USER_ID,)
    task = asyncio.create_task(resolver(bot, CHAT_ID, *args, **kwargs))
    assert await _finish_concurrent(task, bot)
    assert set(bot.calls) == {(CHAT_ID, USER_ID), (CHAT_ID, BOT_ID)}


async def test_group_page_checks_all_visible_groups_concurrently_and_batches_counts():
    bot = ConcurrentBot(expected=len(PAGE_CHAT_IDS) * 2)
    queries = SimpleNamespace(active_campaign_counts_by_chats=AsyncMock(return_value={CHAT_ID: 3}))
    tokens = SimpleNamespace(create_many=AsyncMock(return_value=tuple(map(str, PAGE_CHAT_IDS))))
    renderer = CreatorRenderer(bot=bot, queries=queries, tokens=tokens, sessions=object())
    groups = [
        SimpleNamespace(chat_id=chat_id, id=str(chat_id), title="测试群")
        for chat_id in PAGE_CHAT_IDS
    ]
    task = asyncio.create_task(
        renderer._group_buttons(
            PanelTarget(USER_ID, USER_ID, 10),
            groups=groups,
            selected_chat_ids=(CHAT_ID,),
            context=DraftPanelContext("draft", 1, 10),
        )
    )
    rows = await _finish_concurrent(task, bot)
    queries.active_campaign_counts_by_chats.assert_awaited_once_with(PAGE_CHAT_IDS)
    assert len(rows) == len(PAGE_CHAT_IDS)
    assert "另有 3 个进行中活动" in rows[-1][0].text
    assert rows[-1][0].text.startswith("✅")


def _combined_rules():
    return draft_data() | {
        "group_activity_rules": {str(CHAT_ID): {"enabled": True}},
        "group_invite_rules": {str(CHAT_ID): {"enabled": True}},
        "base_activity_rules": {str(CHAT_ID): {"daily_messages": 2}},
        "admission": {"type": "message", "groups": {str(CHAT_ID): {"daily_messages": 2}}},
    }


async def test_combined_gate_and_two_bonuses_check_same_chat_once():
    bot = ConcurrentBot(expected=2)
    task = asyncio.create_task(
        verify_publication_bonus_permissions(bot, USER_ID, _combined_rules())
    )
    await _finish_concurrent(task, bot)
    bot.get_chat.assert_awaited_once_with(CHAT_ID)
    assert len(bot.calls) == 2


@pytest.mark.parametrize("can_invite,can_send", [(False, True), (True, False)])
async def test_combined_permission_check_keeps_invite_and_speech_requirements(can_invite, can_send):
    bot = SimpleNamespace(
        id=BOT_ID,
        get_chat=AsyncMock(return_value=_chat(can_send=can_send)),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status="administrator", can_invite_users=can_invite)
        ),
    )
    with pytest.raises(UserFacingError, match="邀请权限" if not can_invite else "禁止普通成员发言"):
        await verify_publication_bonus_permissions(bot, USER_ID, _combined_rules())
    assert bot.get_chat_member.await_count == 2


async def test_network_failures_remain_visible_and_no_buttons_are_issued():
    bot = SimpleNamespace(
        id=BOT_ID, get_chat_member=AsyncMock(side_effect=ConnectionError("reset"))
    )
    tokens = SimpleNamespace(create_many=AsyncMock())
    renderer = CreatorRenderer(
        bot=bot,
        tokens=tokens,
        sessions=object(),
        queries=SimpleNamespace(active_campaign_counts_by_chats=AsyncMock(return_value={})),
    )
    with pytest.raises(ConnectionError, match="reset"):
        await renderer._group_buttons(
            PanelTarget(USER_ID, USER_ID, 10),
            groups=[SimpleNamespace(id="group", chat_id=CHAT_ID)],
            selected_chat_ids=(),
            context=DraftPanelContext("draft", 1, 10),
        )
    tokens.create_many.assert_not_awaited()


async def test_select_page_rechecks_only_unselected_groups_and_keeps_failed_group_out():
    groups = [
        SimpleNamespace(chat_id=chat_id, id=str(chat_id), title="群", timezone="Asia/Shanghai")
        for chat_id in PAGE_CHAT_IDS
    ]
    data = draft_data() | {
        "participation_groups": [{"id": CHAT_ID, "title": "已选", "timezone": "Asia/Shanghai"}]
    }
    picker = CreatorGroupPicker(
        bot=object(), sessions=object(), queries=object(), renderer=object()
    )
    picker._required_data = AsyncMock(return_value=data)
    picker._visible_groups = AsyncMock(return_value=groups)
    picker._availability = AsyncMock(
        side_effect=lambda chat_id, _: SimpleNamespace(selectable=chat_id != PAGE_CHAT_IDS[0])
    )
    picker._save_and_render = AsyncMock()
    await picker.select_page(SimpleNamespace(from_user=SimpleNamespace(id=USER_ID)), None)
    assert picker._availability.await_count == len(PAGE_CHAT_IDS) - 1
    updated = picker._save_and_render.await_args.args[1]
    assert {group["id"] for group in updated["participation_groups"]} == set(PAGE_CHAT_IDS[1:])


async def test_failed_gate_save_keeps_original_draft_and_staged_rule():
    from tests.test_creator_settings import ThresholdSessions
    from tg_lottery.telegram.creator_behavior_admission import CreatorBehaviorAdmission

    data = gate.apply_action(_gate_draft(), "draft_gate:group_save")
    before = deepcopy(data)
    sessions = ThresholdSessions(data)
    sessions.state = "draft:behavior_admission"
    renderer = SimpleNamespace(render=AsyncMock())
    bot = SimpleNamespace(
        id=BOT_ID,
        get_chat=AsyncMock(return_value=_chat(can_send=False)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
    )
    admission = CreatorBehaviorAdmission(sessions=sessions, renderer=renderer, bot=bot)
    with pytest.raises(UserFacingError, match="禁止普通成员发言"):
        await admission.handle(
            SimpleNamespace(from_user=SimpleNamespace(id=USER_ID)),
            SimpleNamespace(action="draft_gate:save"),
        )
    assert sessions.state == "draft:behavior_admission"
    assert sessions.data == before
    renderer.render.assert_not_awaited()
