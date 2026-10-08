from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.telegram.help_views import HelpLinks, guide_summary
from tg_lottery.telegram.keyboard_layout import columns, fit_rows, list_rows
from tg_lottery.telegram.member_navigation_context import MemberPageContext, scoped_keyboard


def test_list_layout_preserves_identity_and_payload_for_same_long_names():
    name = "同名很长的活动" * 10
    buttons = [
        InlineKeyboardButton(text=name, callback_data="m:detail:first"),
        InlineKeyboardButton(text=name, callback_data="m:detail:second"),
        InlineKeyboardButton(text=name, url="https://t.me/example"),
        InlineKeyboardButton(text=name, copy_text=CopyTextButton(text="exact private copy")),
    ]
    arranged = list_rows([[b] for b in buttons], start=7)
    flat = [b for row in arranged for b in row]
    assert [b.model_dump(exclude={"text"}) for b in flat] == [
        b.model_dump(exclude={"text"}) for b in buttons
    ]
    assert len({b.text for b in flat}) == 4
    assert all(b.text == name for b in buttons)
    assert [len(row) for row in arranged] == [2, 2]


def test_warning_does_not_hide_its_reason_or_pair_with_valid_selection():
    warning = InlineKeyboardButton(text="\u26a0 频道应设置为准入条件", callback_data="bad")
    ordinary = [InlineKeyboardButton(text=f"群{i}", callback_data=f"group:{i}") for i in range(3)]
    rows = list_rows([[ordinary[0]], [warning], [ordinary[1]], [ordinary[2]]])
    assert rows[1] == [warning]
    assert [b.callback_data for row in rows for b in row] == [
        "group:0",
        "bad",
        "group:1",
        "group:2",
    ]
    assert len(rows[-1]) == 2


def test_fields_are_not_mixed_when_presets_are_split():
    messages = [(str(n), f"messages:{n}") for n in (5, 10, 20, 99)]
    days = [(str(n), f"days:{n}") for n in (1, 3, 7)]
    numeric = [("\u22121", "dec"), ("当前", "custom"), ("+1", "inc")]
    rows = fit_rows([messages, days, numeric])
    assert [len(row) for row in rows] == [2, 2, 2, 1, 3]
    assert [b for row in rows for b in row] == messages + days + numeric


def test_help_topics_and_channel_links_stay_in_separate_sections():
    _, keyboard = guide_summary(HelpLinks("https://t.me/a", "https://t.me/b", "https://t.me/c"))
    rows = keyboard.inline_keyboard
    assert [len(row) for row in rows] == [2, 2, 2, 1, 1]
    assert all(b.callback_data.startswith("nav:guide:") for row in rows[:2] for b in row)
    assert all(b.url for row in rows[2:4] for b in row)
    assert rows[-1][0].callback_data == "nav:index"


async def test_scoped_tokens_follow_original_actions_after_filter_reflow():
    requests = []

    async def issue(**options):
        requests.extend(options["requests"])
        assert options["owner_user_id"] == options["chat_id"] == 201
        return tuple(f"token{i}" for i in range(len(requests)))

    routes = [
        f"m:ir:reference:-1001:0:{state}:0" for state in ("all", "pending", "valid", "invalid")
    ]
    original = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=route, callback_data=route) for route in routes]
        ]
    )
    result = await scoped_keyboard(
        SimpleNamespace(create_many=AsyncMock(side_effect=issue)),
        original,
        context=MemberPageContext("m:ir:reference:-1001:0:all:0"),
        user_id=201,
    )
    assert [request.payload["route"] for request in requests] == routes
    assert [b.callback_data for row in result.inline_keyboard for b in row] == [
        "secure:token0",
        "secure:token1",
        "secure:token2",
        "secure:token3",
    ]
    assert [len(row) for row in result.inline_keyboard] == [2, 2]
    assert len(original.inline_keyboard[0]) == 4
    assert columns([]) == []
