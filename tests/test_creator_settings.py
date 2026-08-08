from types import SimpleNamespace

import pytest

from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_settings import _parse_chat_refs
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


def test_participation_refs_accept_public_username_and_numeric_id() -> None:
    assert _parse_chat_refs("@public_group\uff0c-1003 @public_group") == [
        "@public_group",
        -1003,
    ]
    with pytest.raises(ValueError, match="公开用户名或数字 ID"):
        _parse_chat_refs("invalid-name")


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

    with pytest.raises(ValueError, match="必须是参与条件"):
        await creator._required_chat_record("@public_group")


def test_index_keyboard_contains_real_group_and_channel_links() -> None:
    keyboard = _index_keyboard("lottery_test_bot")
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    urls = {button.text: button.url for button in buttons if button.url}

    assert urls["添加到群组"] == "https://t.me/lottery_test_bot?startgroup=lottery"
    assert urls["添加到频道"] == "https://t.me/lottery_test_bot?startchannel=lottery"


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
