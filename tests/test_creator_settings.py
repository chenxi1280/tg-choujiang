from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.types import User

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.campaign_creator import CampaignCreator, _validate_activity_chat
from tg_lottery.telegram.creator_participation import (
    parse_chat_refs,
    parse_positions,
    participation_add_text,
    participation_rows,
    participation_text,
)
from tg_lottery.telegram.creator_settings import (
    CreatorSettings,
    custom_draw_prompt,
)
from tg_lottery.telegram.issuer import creator_issuer, resolve_issuer
from tg_lottery.telegram.personal_flow import _index_keyboard


class RequiredChatBot:
    id = 9001

    def __init__(self, status: str = "administrator") -> None:
        self._status = status

    async def get_chat(self, ref: int | str):
        mapping = {"@public_group": -1002, -1003: -1003, -1001: -1001}
        chat_id = mapping[ref]
        return SimpleNamespace(
            id=chat_id,
            title=f"群组 {chat_id}",
            full_name=None,
            type="supergroup",
            username="public_group" if ref == "@public_group" else None,
        )

    async def get_chat_member(self, chat_id: int, user_id: int):
        return SimpleNamespace(status=self._status)


class RecordingSessions:
    def __init__(self) -> None:
        self.state = ""
        self.data = {}

    async def set(self, user_id, state, data) -> None:
        self.state = state
        self.data = data


class RecordingRenderer:
    async def edit(self, target, *, text, rows) -> None:
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


def test_participation_refs_accept_public_username_and_numeric_id() -> None:
    assert parse_chat_refs("@public_group\uff0c-1003 @public_group") == [
        "@public_group",
        -1003,
    ]
    with pytest.raises(UserFacingError, match="公开用户名或数字 ID"):
        parse_chat_refs("invalid-name")


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

    assert rows[0] == [("添加参与条件", "draft_participation_add")]
    assert "请回复要添加的群聊/频道" in participation_add_text()
    assert (
        settings._grant_handlers()["draft_participation_add"].__name__ == "_edit_participation_add"
    )


def test_activity_bonus_rejects_a_channel_as_the_main_chat() -> None:
    with pytest.raises(UserFacingError, match="频道无法统计成员发言"):
        _validate_activity_chat({"bonus_types": ["activity"]}, {"type": "channel"})
    _validate_activity_chat({"bonus_types": ["invite"]}, {"type": "channel"})


async def test_required_chats_are_resolved_deduplicated_and_frozen() -> None:
    creator = _creator(RequiredChatBot())

    records = await creator._resolve_required_chats(
        {
            "chat_id": -1001,
            "required_chat_refs": ["@public_group", -1003, -1001],
        }
    )

    assert [item["id"] for item in records] == [-1002, -1003]
    assert records[0]["title"] == "群组 -1002"


async def test_required_chat_rejects_bot_without_admin_access() -> None:
    creator = _creator(RequiredChatBot(status="member"))

    with pytest.raises(UserFacingError, match="必须是每个参与条件"):
        await creator._required_chat_record("@public_group")


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
    data = {"chat_id": -1001, "panel_message_id": 9, "required_chats": chats}

    await settings._receive_participation_delete(_message("1,3"), data)

    assert [item["id"] for item in sessions.data["required_chats"]] == [-1003]
    assert sessions.data["required_chat_refs"] == [-1003]
    assert sessions.data["required_chat_ids"] == [-1003]


async def test_legacy_last_draft_required_chat_ids_are_restored_for_the_ui() -> None:
    settings = CreatorSettings(
        sessions=RecordingSessions(),
        renderer=RecordingRenderer(),
        profiles=object(),
        queries=object(),
        bot=RequiredChatBot(),
    )

    restored = await settings._restore_required_chats(
        {"required_chat_ids": [-1003], "required_chat_refs": [], "required_chats": []}
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
    keyboard = _index_keyboard("lottery_test_bot")
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    urls = {button.text: button.url for button in buttons if button.url}

    assert urls["添加到群组"] == "https://t.me/lottery_test_bot?startgroup=lottery"
    assert urls["添加到频道"] == "https://t.me/lottery_test_bot?startchannel=lottery"


def test_custom_draw_prompt_uses_an_example_instead_of_a_python_format_string() -> None:
    now = datetime(2026, 8, 10, 13, tzinfo=UTC)

    prompt = custom_draw_prompt("Asia/Shanghai", now=now)

    assert prompt == "🕒 回复开奖时间 (北京时间)\n\n例如: 2026-08-11 20:00"
    assert "%Y" not in prompt


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
