from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from tg_lottery.domain.draft_contract import DRAFT_SESSION_TTL_HOURS
from tg_lottery.domain.errors import RecoverableDraftCallback
from tg_lottery.telegram.command_input import parse_bot_command
from tg_lottery.telegram.help_views import DRAFT_UNAVAILABLE_TEXT, GUIDE_TEXT
from tg_lottery.telegram.update_handler import LotteryUpdateHandler

BOT_USERNAME = "lottery_bot"
USER_ID = 201


def _handler() -> LotteryUpdateHandler:
    handler = object.__new__(LotteryUpdateHandler)
    handler._bot_username = BOT_USERNAME
    handler._personal_flow = SimpleNamespace(
        show_index=AsyncMock(),
        show_guide=AsyncMock(),
        handle_command=AsyncMock(return_value=False),
    )
    handler._admin_flow = SimpleNamespace(
        suspend_draft=AsyncMock(),
        start_create_message=AsyncMock(),
        handle_text=AsyncMock(return_value=True),
        recover_draft_callback=AsyncMock(return_value=False),
    )
    handler._member_flow = SimpleNamespace(
        register=AsyncMock(),
        _queries=SimpleNamespace(campaign_by_reference=AsyncMock(return_value=None)),
    )
    handler._queries = handler._member_flow._queries
    return handler


async def test_plain_start_opens_short_home_without_full_guide() -> None:
    handler = _handler()
    message = SimpleNamespace(from_user=SimpleNamespace(id=USER_ID), text="/start")

    await handler._private_message(message)

    handler._personal_flow.show_index.assert_awaited_once_with(message)
    handler._personal_flow.show_guide.assert_not_awaited()


async def test_start_deep_link_keeps_registration_route() -> None:
    handler = _handler()
    message = SimpleNamespace(from_user=SimpleNamespace(id=USER_ID), text="/start join_campaign")

    await handler._private_message(message)

    handler._member_flow.register.assert_awaited_once_with(message, "campaign")
    handler._personal_flow.show_index.assert_not_awaited()


async def test_other_command_never_triggers_start_registration() -> None:
    handler = _handler()
    message = SimpleNamespace(from_user=SimpleNamespace(id=USER_ID), text="/starting join_campaign")

    await handler._private_message(message)

    handler._member_flow.register.assert_not_awaited()
    handler._admin_flow.handle_text.assert_awaited_once_with(message)


@pytest.mark.parametrize("name", ["start", "new", "menu", "help", "region"])
async def test_command_for_other_bot_never_enters_current_draft(name: str) -> None:
    handler = _handler()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=USER_ID), text=f"/{name}@other_bot join_campaign"
    )

    await handler._private_message(message)

    handler._member_flow.register.assert_not_awaited()
    handler._admin_flow.handle_text.assert_not_awaited()
    handler._personal_flow.handle_command.assert_not_awaited()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("ordinary text", None),
        ("/start", ("/start", "", "")),
        ("/start@LOTTERY_BOT join_campaign", ("/start", "join_campaign", "LOTTERY_BOT")),
        ("/start\njoin_campaign", ("/start", "join_campaign", "")),
        ("/start_extra join_campaign", ("/start_extra", "join_campaign", "")),
    ],
)
def test_command_parser_preserves_exact_name_and_recipient(
    text: str, expected: tuple | None
) -> None:
    parsed = parse_bot_command(text)
    actual = (parsed.name, parsed.argument, parsed.recipient) if parsed is not None else None
    assert actual == expected


@pytest.mark.parametrize("name", ["new", "menu"])
async def test_current_bot_mentioned_commands_keep_navigation(name: str) -> None:
    handler = _handler()
    handler._admin_flow.start_create_message = AsyncMock()
    message = SimpleNamespace(from_user=SimpleNamespace(id=USER_ID), text=f"/{name}@LOTTERY_BOT")

    await handler._private_message(message)

    if name == "new":
        handler._admin_flow.start_create_message.assert_awaited_once_with(message)
    else:
        handler._personal_flow.show_index.assert_awaited_once_with(message)


async def test_unavailable_draft_explains_actual_retention() -> None:
    handler = _handler()
    handler._tokens = SimpleNamespace(
        consume=AsyncMock(side_effect=RecoverableDraftCallback("expired"))
    )
    message = Message.model_validate(
        {
            "message_id": 10,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": USER_ID, "type": "private"},
            "text": "old panel",
        }
    )
    query = SimpleNamespace(
        message=message, from_user=SimpleNamespace(id=USER_ID), answer=AsyncMock()
    )

    await handler._dispatch_secure_callback(query, "secure:expired")

    query.answer.assert_awaited_once_with(DRAFT_UNAVAILABLE_TEXT, show_alert=True)
    assert f"{DRAFT_SESSION_TTL_HOURS} 小时" in DRAFT_UNAVAILABLE_TEXT
    assert "7 天" not in DRAFT_UNAVAILABLE_TEXT
    assert "最后一次更新" in GUIDE_TEXT
    assert "新建活动按北京时间定时开奖" in GUIDE_TEXT


async def test_group_help_directs_user_to_private_bot() -> None:
    handler = _handler()
    message = SimpleNamespace(chat=SimpleNamespace(id=-100123), answer=AsyncMock())

    await handler._group_command(message, "/help@LOTTERY_BOT")

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == "该命令请在私聊中使用。"
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].url == (
        f"https://t.me/{BOT_USERNAME}"
    )
