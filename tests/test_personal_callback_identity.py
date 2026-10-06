from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from tg_lottery.telegram.help_views import HelpLinks, guide_keyboard
from tg_lottery.telegram.personal_flow import PersonalFlow

USER_ID = 202
BOT_ID = 9001


def _query(*, chat_id: int = USER_ID, chat_type: str = "private") -> CallbackQuery:
    return CallbackQuery(
        id="callback",
        from_user=User(id=USER_ID, is_bot=False, first_name="Member"),
        chat_instance="private-test",
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=BOT_ID, is_bot=True, first_name="Lottery"),
            text="Menu",
        ),
    )


@pytest.mark.parametrize(
    "callback,method",
    [
        ("nav:index", "show_index"),
        ("nav:join", "show_joined"),
        ("nav:join:active:0", "show_joined"),
        ("nav:invites", "show_invitation_campaigns"),
        ("nav:invites:ended:0", "show_invitation_campaigns"),
        ("nav:created:active", "_show_created"),
        ("nav:region", "show_regions"),
        ("nav:myid", "show_user_id"),
    ],
)
async def test_personal_callback_uses_clicking_user(callback, method) -> None:
    flow = PersonalFlow(profiles=None, queries=None, bot_username="lottery_bot")
    handler = AsyncMock()
    setattr(flow, method, handler)
    query = _query()
    assert await flow.handle_callback(query, callback)
    assert handler.call_args.args[0].from_user.id == USER_ID
    assert query.message.from_user.id == BOT_ID


async def test_personal_callback_does_not_open_another_users_menu() -> None:
    flow = PersonalFlow(profiles=None, queries=None, bot_username="lottery_bot")
    flow.show_index = AsyncMock()
    assert not await flow.handle_callback(_query(chat_id=303), "nav:index")
    assert not await flow.handle_callback(
        _query(chat_id=-1001, chat_type="supergroup"), "nav:index"
    )
    flow.show_index.assert_not_awaited()


async def test_help_is_accessible_by_command_and_includes_configured_channels() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    links = HelpLinks(guide="https://t.me/test_guide", navigation="https://t.me/test_nav")
    flow = PersonalFlow(profiles=None, queries=None, bot_username="lottery_bot", help_links=links)
    assert await flow.handle_command(message, "/help")
    assert "选择你正在办理的事项" in message.answer.call_args.args[0]
    markup = message.answer.call_args.kwargs["reply_markup"]
    urls = [button.url for row in markup.inline_keyboard for button in row if button.url]
    assert urls == [links.guide, links.navigation]
    assert all(
        button.url is None for row in guide_keyboard(HelpLinks()).inline_keyboard for button in row
    )
