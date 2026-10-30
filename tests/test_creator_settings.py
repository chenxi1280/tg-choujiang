from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_group_selection import resolve_requirements
from tg_lottery.telegram.creator_participation import (
    parse_chat_refs,
    parse_positions,
    participation_add_text,
    participation_rows,
    participation_text,
    validate_main_threshold,
)
from tg_lottery.telegram.creator_required_chats_data import restore_required_chats
from tg_lottery.telegram.creator_settings import (
    CreatorSettings,
    custom_draw_prompt,
)
from tg_lottery.telegram.issuer import creator_issuer, resolve_issuer
from tg_lottery.telegram.personal_flow import _index_keyboard
from tg_lottery.telegram.ui_style import plain_label


@pytest.mark.parametrize("text", ["/清空说明", "/clear"])
async def test_description_clear_command_keeps_other_draft_fields(text):
    settings = object.__new__(CreatorSettings)
    settings._dashboard_from_message = AsyncMock()
    message = SimpleNamespace(text=text)
    await settings._receive_description(message, {"description": "旧说明", "title": "活动"})
    settings._dashboard_from_message.assert_awaited_once_with(
        message, {"description": "", "title": "活动"}
    )


class RequiredChatBot:
    id = 9001

    def __init__(
        self,
        status: str = "administrator",
        *,
        creator_status: str = "administrator",
    ) -> None:
        self._status = status
        self._creator_status = creator_status

    async def get_chat(self, ref: int | str):
        mapping = {"@public_group": -1002, -1003: -1003, -1001: -1001}
        chat_id = mapping[ref]
        return SimpleNamespace(
            id=chat_id,
            title=f"群组 {chat_id}",
            full_name=None,
            type="supergroup",
            username="public_group" if ref == "@public_group" else None,
            permissions=SimpleNamespace(can_send_messages=True),
        )

    async def get_chat_member(self, chat_id: int, user_id: int):
        status = self._status if user_id == self.id else self._creator_status
        return SimpleNamespace(status=status)


class RecordingSessions:
    def __init__(self) -> None:
        self.state = ""
        self.data = {}

    async def set(self, user_id, state, data) -> None:
        self.state = state
        self.data = data


class ThresholdSessions:
    def __init__(self, data: dict) -> None:
        self.state = "draft:participation"
        self.data = data

    async def get(self, user_id: int):
        return SimpleNamespace(state=self.state, data=self.data)

    async def set(self, user_id: int, state: str, data: dict) -> None:
        self.state = state
        self.data = data


class RecordingRenderer:
    async def edit(self, target, *, text, rows) -> None:
        self.text = text
        self.rows = rows

    async def render(self, message, *, user_id, text, rows) -> None:
        self.text = text
        self.rows = rows


class IssuerBot:
    async def get_chat(self, ref):
        assert ref == 302
        return SimpleNamespace(
            id=302,
            type="private",
            username="prize_owner",
            full_name="Prize Owner",
            title=None,
        )


def test_participation_refs_accept_links_username_and_advanced_chat_id() -> None:
    assert parse_chat_refs("https://t.me/public_group,t.me/c/123456/77 -1003 @public_group") == [
        "@public_group",
        -100123456,
        -1003,
    ]
    with pytest.raises(UserFacingError, match="请发送群聊/频道链接"):
        parse_chat_refs("invalid-name")
    with pytest.raises(UserFacingError, match="私有邀请链接无法识别"):
        parse_chat_refs("https://t.me/+not_resolvable")


def test_participation_delete_accepts_specific_positions() -> None:
    assert parse_positions("3\uff0c1 3", 3) == (3, 1)
    with pytest.raises(UserFacingError, match="删除编号必须"):
        parse_positions("4", 3)


def test_participation_conditions_are_paginated_without_a_product_cap() -> None:
    chats = [
        {"id": -1000 - index, "title": f"条件 {index}", "private": True} for index in range(1, 13)
    ]
    data = {"required_chats": chats, "participation_page": 1}

    text = participation_text(data)
    rows = participation_rows(data)

    assert "共 12 项 · 第 2/2 页" in text
    assert "11. 加入群聊 条件 11" in text
    assert "\n1. 加入群聊 条件 1" not in text
    assert rows[1] == [("← 上一页", "draft_participation_prev")]


def test_participation_add_button_opens_a_distinct_input_prompt() -> None:
    rows = participation_rows({"required_chats": []})
    settings = CreatorSettings(
        sessions=RecordingSessions(),
        renderer=RecordingRenderer(),
        profiles=object(),
        queries=object(),
        bot=RequiredChatBot(),
    )

    assert rows[0] == [("添加准入条件", "draft_participation_add")]
    assert "也可直接发送群聊或频道链接" in participation_add_text()
    assert "参与活动的群无需在这里重复添加" in participation_add_text()
    assert "创建者和机器人都必须" in participation_add_text()
    assert "数字 chat ID 仅用于高级操作" in participation_add_text()
    assert (
        settings._grant_handlers()["draft_participation_add"].__name__ == "_edit_participation_add"
    )


def test_activity_bonus_rejects_a_channel_as_the_main_chat() -> None:
    with pytest.raises(UserFacingError, match=r"频道.*无法统计发言"):
        validate_main_threshold(
            {"chat_message_thresholds": {"-1001": 1}},
            {"id": -1001, "type": "channel"},
        )
    validate_main_threshold({}, {"id": -1001, "type": "channel"})


async def test_required_chats_reject_participation_group_overlap() -> None:
    with pytest.raises(UserFacingError, match=r"群组 -1001.*参与群.*准入条件"):
        await resolve_requirements(
            RequiredChatBot(),
            {
                "chat_id": -1001,
                "required_chat_refs": ["@public_group", -1003, -1001],
            },
            creator_user_id=201,
        )


async def test_required_chats_are_resolved_and_frozen() -> None:
    records = await resolve_requirements(
        RequiredChatBot(),
        {"chat_id": -1001, "required_chat_refs": ["@public_group", -1003]},
        creator_user_id=201,
    )

    assert [item["id"] for item in records] == [-1002, -1003]
    assert records[0]["title"] == "群组 -1002"


async def test_activity_bonus_rejects_mandatory_group_without_member_speech() -> None:
    bot = RequiredChatBot()
    original_get_chat = bot.get_chat

    async def get_chat(ref: int | str):
        chat = await original_get_chat(ref)
        chat.permissions.can_send_messages = False
        return chat

    bot.get_chat = get_chat
    with pytest.raises(UserFacingError, match=r"群组 -1002.*禁止普通成员发言"):
        await resolve_requirements(
            bot,
            {
                "chat_id": -1001,
                "required_chat_refs": ["@public_group"],
                "bonus_types": ["activity"],
            },
            creator_user_id=201,
        )


async def test_required_chat_rejects_bot_without_admin_access() -> None:
    with pytest.raises(UserFacingError, match=r"群组 -1002.*设为管理员"):
        await resolve_requirements(
            RequiredChatBot(status="member"),
            {"chat_id": -1001, "required_chat_refs": ["@public_group"]},
            creator_user_id=201,
        )


async def test_required_chat_rejects_creator_without_admin_access() -> None:
    with pytest.raises(UserFacingError, match=r"群组 -1002.*创建者.*管理员"):
        await resolve_requirements(
            RequiredChatBot(creator_status="member"),
            {"chat_id": -1001, "required_chat_refs": ["@public_group"]},
            creator_user_id=201,
        )


async def test_participation_conditions_accumulate_by_resolved_chat_id() -> None:
    sessions = RecordingSessions()
    settings = CreatorSettings(
        sessions=sessions,
        renderer=RecordingRenderer(),
        profiles=object(),
        queries=object(),
        bot=RequiredChatBot(),
    )
    data = {"chat_id": -1001, "panel_message_id": 9, "required_chats": []}

    await settings._receive_participation(_message("@public_group,-1003"), data)

    assert [item["id"] for item in sessions.data["required_chats"]] == [-1002, -1003]
    assert sessions.data["required_chat_ids"] == [-1002, -1003]
    with pytest.raises(UserFacingError, match="已经在参与条件"):
        await settings._receive_participation(_message("@public_group"), sessions.data)

    with pytest.raises(UserFacingError, match="已经是参与活动的群"):
        await settings._receive_participation(_message("-1001"), sessions.data)


async def test_participation_conditions_delete_selected_positions() -> None:
    sessions = RecordingSessions()
    renderer = RecordingRenderer()
    settings = CreatorSettings(
        sessions=sessions,
        renderer=renderer,
        profiles=object(),
        queries=object(),
        bot=RequiredChatBot(),
    )
    chats = [
        {"id": -1002, "title": "条件 1", "type": "supergroup", "private": True},
        {"id": -1003, "title": "条件 2", "type": "channel", "private": True},
        {"id": -1004, "title": "条件 3", "type": "supergroup", "private": True},
    ]
    data = {
        "chat_id": -1001,
        "panel_message_id": 9,
        "required_chats": chats,
        "chat_message_thresholds": {"-1002": 10, "-1003": 5},
    }

    await settings._receive_participation_delete(_message("1,3"), data)

    assert [item["id"] for item in sessions.data["required_chats"]] == [-1003]
    assert sessions.data["required_chat_refs"] == [-1003]
    assert sessions.data["required_chat_ids"] == [-1003]
    assert sessions.data["chat_message_thresholds"] == {"-1003": 5}


async def test_legacy_last_draft_required_chat_ids_are_restored_for_the_ui() -> None:
    restored = await restore_required_chats(
        RequiredChatBot(),
        {"required_chat_ids": [-1003], "required_chat_refs": [], "required_chats": []},
        user_id=201,
    )

    assert [item["id"] for item in restored["required_chats"]] == [-1003]
    assert restored["required_chat_refs"] == [-1003]
    assert restored["required_chat_ids"] == [-1003]


async def test_issuer_is_resolved_and_frozen_before_publication() -> None:
    creator = User(id=201, is_bot=False, first_name="Creator", username="creator")

    custom = await resolve_issuer(IssuerBot(), "@prize_owner", creator, candidate_user_ids=(302,))

    assert custom == {
        "issuer": "@prize_owner",
        "issuer_user_id": 302,
        "issuer_username_at_publish": "prize_owner",
        "issuer_display_name_at_publish": "Prize Owner",
        "issuer_name": "Prize Owner",
        "issuer_source": "custom",
    }
    assert creator_issuer(creator)["issuer_source"] == "creator"


def test_index_keyboard_contains_real_group_and_channel_links() -> None:
    from tg_lottery.telegram.help_views import guide_topic

    home = _index_keyboard("lottery_test_bot")
    assert sum(len(row) for row in home.inline_keyboard) == 5
    _, keyboard = guide_topic("create", bot_username="lottery_test_bot")
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    urls = {plain_label(button.text): button.url for button in buttons if button.url}

    assert urls["添加到群组"] == "https://t.me/lottery_test_bot?startgroup=lottery"
    assert urls["添加到频道"] == "https://t.me/lottery_test_bot?startchannel=lottery"


def test_custom_draw_prompt_uses_an_example_instead_of_a_python_format_string() -> None:
    now = datetime(2026, 8, 10, 13, tzinfo=UTC)

    prompt = custom_draw_prompt("Asia/Shanghai", now=now)

    assert "定时开奖 (北京时间)" in prompt
    assert "<code>2026-08-11 20:00</code>" in prompt
    assert "%Y" not in prompt


def _threshold_settings(sessions: ThresholdSessions) -> CreatorSettings:
    return CreatorSettings(
        sessions=sessions,
        renderer=RecordingRenderer(),
        profiles=object(),
        queries=object(),
        bot=RequiredChatBot(),
    )


def _panel_query() -> SimpleNamespace:
    message = Message(
        message_id=9,
        date=datetime(2026, 8, 15, tzinfo=UTC),
        chat=Chat(id=201, type="private"),
    )
    return SimpleNamespace(
        from_user=User(id=201, is_bot=False, first_name="Creator"), message=message
    )


def _threshold_grant(action: str) -> CallbackGrant:
    return CallbackGrant(action, None, {})


async def test_change_chat_threshold_inc_noop_and_dec_to_unset() -> None:
    sessions = ThresholdSessions({"chat_message_thresholds": {"-100123": 1}})
    settings = _threshold_settings(sessions)

    await settings._change_chat_threshold(
        _panel_query(), _threshold_grant("draft_chat_threshold:-100123:inc")
    )
    assert sessions.data["chat_message_thresholds"] == {"-100123": 2}

    await settings._change_chat_threshold(
        _panel_query(), _threshold_grant("draft_chat_threshold:-100123:noop")
    )
    assert sessions.data["chat_message_thresholds"] == {"-100123": 2}

    await settings._change_chat_threshold(
        _panel_query(), _threshold_grant("draft_chat_threshold:-100123:dec")
    )
    await settings._change_chat_threshold(
        _panel_query(), _threshold_grant("draft_chat_threshold:-100123:dec")
    )
    assert sessions.data["chat_message_thresholds"] == {}


async def test_change_chat_threshold_caps_at_the_product_limit() -> None:
    sessions = ThresholdSessions({"chat_message_thresholds": {"-100123": 500}})
    settings = _threshold_settings(sessions)

    with pytest.raises(UserFacingError, match="发言门槛最高 500 条"):
        await settings._change_chat_threshold(
            _panel_query(), _threshold_grant("draft_chat_threshold:-100123:inc")
        )


async def test_chat_threshold_grants_are_routed_by_prefix() -> None:
    sessions = ThresholdSessions({"chat_message_thresholds": {}})
    settings = _threshold_settings(sessions)

    assert settings.handles_action("draft_chat_threshold:-100123:noop")
    assert await settings.handle_grant(
        _panel_query(), _threshold_grant("draft_chat_threshold:-100123:inc")
    )
    assert sessions.data["chat_message_thresholds"] == {"-100123": 1}
    assert sessions.state == "draft:participation"


def _creator(bot: RequiredChatBot) -> CampaignCreator:
    return CampaignCreator(
        bot=bot,
        bot_username="lottery_test_bot",
        sessions=None,
        campaigns=None,
        queries=None,
        renderer=None,
        settings=None,
        profiles=None,
    )


def _message(text: str):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=201),
        chat=SimpleNamespace(id=201),
    )


async def test_per_group_activity_does_not_require_speech_in_join_only_groups() -> None:
    bot = RequiredChatBot()
    original_get_chat = bot.get_chat

    async def get_chat(ref):
        chat = await original_get_chat(ref)
        chat.permissions.can_send_messages = False
        return chat

    bot.get_chat = get_chat
    records = await resolve_requirements(
        bot,
        {
            "chat_id": -1001,
            "required_chat_refs": ["@public_group"],
            "bonus_types": ["activity"],
            "group_activity_rules": {"-1001": {"enabled": True}},
        },
        creator_user_id=201,
    )
    assert records[0]["id"] == -1002
    assert records[0]["member_can_send_messages"] is False
