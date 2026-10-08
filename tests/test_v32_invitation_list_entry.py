from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_lottery.application.participant_records import ParticipatedCampaignPage
from tg_lottery.telegram.participant_list_views import (
    build_participated_campaign_list,
    parse_invitation_list_callback,
)
from tg_lottery.telegram.personal_flow import PersonalFlow
from tg_lottery.telegram.ui_style import plain_label


@pytest.mark.parametrize("category,page", [("active", 0), ("pending", 2), ("ended", 1)])
def test_participated_list_exposes_invitation_entry_with_original_source(category, page):
    source = ParticipatedCampaignPage(category, page, page + 1, ())
    text, keyboard = build_participated_campaign_list(source, invitation_only=False)
    assert text.startswith("🙋 我参加的抽奖\n")
    entry = next(
        button
        for row in keyboard.inline_keyboard
        for button in row
        if plain_label(button.text) == "我的邀请记录"
    )
    assert entry.callback_data == f"nav:invites:{category}:0:{category}:{page}"
    assert parse_invitation_list_callback(entry.callback_data.removeprefix("nav:invites:")) == (
        category,
        0,
        (category, page),
    )


def test_invitation_filters_and_paging_keep_original_participated_list():
    page = ParticipatedCampaignPage("pending", 1, 3, ())
    text, keyboard = build_participated_campaign_list(
        page, invitation_only=True, parent=("ended", 2)
    )
    assert text.startswith("🧾 我的邀请记录\n")
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert next(
        button.callback_data for button in buttons if plain_label(button.text) == "返回参与列表"
    ) == ("nav:join:ended:2")
    navigation = [
        button.callback_data
        for button in buttons
        if button.callback_data.startswith("nav:invites:")
    ]
    assert "nav:invites:pending:2:ended:2" in navigation
    assert "nav:invites:active:0:ended:2" in navigation
    assert all(value.endswith(":ended:2") for value in navigation)
    assert not any(plain_label(button.text) == "我的邀请记录" for button in buttons)


async def test_personal_invitation_handler_renders_return_to_actual_list_source():
    queries = SimpleNamespace(
        participated_campaign_page=AsyncMock(
            return_value=ParticipatedCampaignPage("ended", 0, 1, ())
        )
    )
    message = SimpleNamespace(from_user=SimpleNamespace(id=202), edit_text=AsyncMock())
    flow = PersonalFlow(profiles=None, queries=queries, bot_username="test_bot")
    await flow._show_invitation_list_callback(message, "nav:invites:ended:0:ended:1")
    queries.participated_campaign_page.assert_awaited_once_with(
        202, category="ended", page=0, invitation_only=True
    )
    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    assert any(
        button.callback_data == "nav:join:ended:1"
        for row in keyboard.inline_keyboard
        for button in row
    )


@pytest.mark.parametrize(
    "value", ["ended:1:bogus:0", "ended:-1:ended:1", "ended:0:active", "ended:0:active:0:extra"]
)
def test_invitation_source_rejects_malformed_routes(value):
    with pytest.raises(ValueError):
        parse_invitation_list_callback(value)
