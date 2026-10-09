from datetime import UTC, datetime

from tests.test_telegram_gateway_messages import FakeCardBot, _campaign_payload
from tg_lottery.telegram.creator_schedule import quick_draw_at
from tg_lottery.telegram.creator_settings_ui import custom_draw_prompt, custom_draw_rows
from tg_lottery.telegram.draft_config import parse_draw_at
from tg_lottery.telegram.gateway import TelegramGateway, _campaign_card_text, _campaign_keyboard
from tg_lottery.telegram.gateway_requests import GatewayRequest
from tg_lottery.telegram.public_projection import format_public_time


def test_custom_time_is_beijing_even_for_a_group_in_another_timezone():
    moment = parse_draw_at("2026-09-15 20:30", "Europe/London")
    assert moment.astimezone(UTC) == datetime(2026, 9, 15, 12, 30, tzinfo=UTC)
    assert "北京时间" in custom_draw_prompt("Europe/London")
    assert custom_draw_rows("Europe/London")[0][0] == ("7天后", "draft_draw_week")


def test_quick_ten_days_and_legacy_naive_time_preserve_correct_instants():
    now = datetime(2026, 9, 14, 9, tzinfo=UTC)
    target = quick_draw_at("draft_draw_ten_days", "Europe/London", now=now)
    assert target.astimezone(UTC) == datetime(2026, 9, 24, 9, tzinfo=UTC)
    assert format_public_time("2026-09-14 10:00", "Europe/London") == (
        "2026年09月14日 17:00 (北京时间)"
    )


def test_shared_card_detail_callback_and_first_start_link_bind_same_activity():
    payload = _campaign_payload()
    button = _campaign_keyboard(payload).inline_keyboard[0][0]
    assert button.callback_data == f"m:detail:{payload['campaign_ref']}"
    assert button.url is None
    first_start_link = (
        f"https://t.me/{payload['bot_username']}?start=join_{payload['campaign_ref']}"
    )
    assert first_start_link in _campaign_card_text(payload)
    assert _campaign_keyboard(payload).inline_keyboard[0][1].url == (
        f"https://t.me/{payload['bot_username']}"
    )


async def test_draw_result_notifies_and_mentions_winner_without_username():
    bot = FakeCardBot()
    payload = {
        "campaign_name": "开奖提醒",
        "extended_tips": False,
        "winners": [
            {
                "user_id": 123,
                "display_name": "小明<中奖>",
                "username": None,
                "prize_position": 1,
                "prize_name": "礼品",
                "prize_slot_no": 1,
            }
        ],
    }
    await TelegramGateway(bot, object()).execute(GatewayRequest("send_draw_result", -1001, payload))
    _, (_, text), options = bot.sent[0]
    assert '<a href="tg://user?id=123">@小明&lt;中奖&gt;</a>' in text
    assert options["parse_mode"] == "HTML"
    assert options["disable_notification"] is False


async def test_winner_private_notice_is_not_silent():
    bot = FakeCardBot()
    payload = {
        "campaign_name": "开奖提醒",
        "prize_name": "礼品",
        "prize_slot_no": 1,
        "issuer_user_id": 456,
        "issuer_name": "发奖人",
        "claim_instructions": "联系发奖人",
    }
    await TelegramGateway(bot, object()).execute(GatewayRequest("send_winner_notice", 123, payload))
    _, (chat_id, text), options = bot.sent[0]
    assert chat_id == 123
    assert "恭喜你" in text
    assert options["disable_notification"] is False


async def test_quick_time_buttons_keep_callback_authorization_order():
    from unittest.mock import AsyncMock

    from tg_lottery.application.admin_sessions import DraftPanelContext
    from tg_lottery.telegram.creator_renderer import CreatorRenderer, PanelTarget

    tokens = AsyncMock()
    tokens.create_many.return_value = (
        "hour-token",
        "evening-token",
        "tomorrow-token",
        "back-token",
    )
    sessions = AsyncMock()
    sessions.issue_panel_context.return_value = DraftPanelContext("draft", 1, 9)
    renderer = CreatorRenderer(bot=object(), tokens=tokens, queries=object(), sessions=sessions)
    keyboard = await renderer._keyboard(PanelTarget(123, 123, 9), custom_draw_rows("Europe/London"))
    assert keyboard.inline_keyboard[0][0].copy_text is None
    assert keyboard.inline_keyboard[0][0].callback_data == "secure:hour-token"
    assert keyboard.inline_keyboard[-1][0].callback_data == "secure:back-token"
    requests = tokens.create_many.call_args.kwargs["requests"]
    assert [request.action for request in requests] == [
        "draft_draw_week",
        "draft_draw_ten_days",
        "draft_draw_tomorrow",
        "draft_back",
    ]
