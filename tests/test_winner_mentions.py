from copy import deepcopy

import pytest

from tests.test_telegram_gateway_messages import FakeCardBot
from tg_lottery.presentation.draw_messages import draw_result_pages, draw_result_text
from tg_lottery.presentation.mentions import (
    CAPTION_UTF16_LIMIT,
    MESSAGE_UTF16_LIMIT,
    html_utf16_length,
    user_mention,
)
from tg_lottery.telegram.gateway import TelegramGateway, _campaign_state_card_text
from tg_lottery.telegram.gateway_requests import GatewayRequest


def _winner(index=1, **overrides):
    return {
        "user_id": index,
        "username": f"winner_{index}",
        "display_name": "测试中奖人",
        "prize_position": 1,
        "prize_slot_no": index,
        "prize_name": "纪念奖",
    } | overrides


@pytest.mark.parametrize("username,label", [("chosen", "@chosen"), (None, "@同名用户")])
def test_mentions_bind_exact_winner_identity_with_or_without_username(username, label):
    assert user_mention(_winner(101, username=username, display_name="同名用户")) == (
        f'<a href="tg://user?id=101">{label}</a>'
    )
    assert "id=202" in user_mention(_winner(202, username=username, display_name="同名用户"))


async def test_real_gateway_arguments_enable_mentions_and_escape_all_user_content():
    bot = FakeCardBot()
    gateway = TelegramGateway(bot, object())
    payload = {
        "campaign_name": "活动 <b>&😀",
        "extended_tips": True,
        "winners": [
            _winner(username=None, display_name='<a href="fake">用户</a>', prize_name="奖品 <i>&")
        ],
        "undistributed": [{"prize_position": 2, "prize_name": "未分配 <b>", "count": 1}],
    }
    await gateway.execute(GatewayRequest("send_draw_result", -1001, payload))
    text, kwargs = bot.sent[0][1][1], bot.sent[0][2]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["link_preview_options"].is_disabled is True
    assert "活动 &lt;b&gt;&amp;😀" in text
    assert "奖品 &lt;i&gt;&amp;" in text
    assert "未分配 &lt;b&gt;" in text
    assert '<a href="tg://user?id=1">@&lt;a href=&quot;fake&quot;&gt;用户&lt;/a&gt;</a>' in text
    assert '<a href="fake">' not in text


@pytest.mark.parametrize("tips", [True, False])
async def test_winner_notice_always_has_clickable_issuer_and_escaped_claim_instructions(tips):
    bot = FakeCardBot()
    payload = {
        "campaign_name": "活动 <b>",
        "prize_name": "奖品 &",
        "prize_slot_no": 1,
        "claim_instructions": "联系 <管理员>",
        "extended_tips": tips,
        "issuer_name": "发奖人",
        "issuer_user_id": 101,
        "issuer_username": "issuer",
    }
    await TelegramGateway(bot, object()).execute(GatewayRequest("send_winner_notice", 202, payload))
    assert 'href="tg://user?id=101">@issuer</a>' in bot.sent[0][1][1]
    assert "联系 &lt;管理员&gt;" in bot.sent[0][1][1]
    assert bot.sent[0][2]["parse_mode"] == "HTML"


def test_long_results_have_complete_nonduplicated_pages_with_utf16_safe_mentions():
    winners = [
        _winner(index, username=None, display_name="😀<&>" * 30, prize_name="大奖😀" * 20)
        for index in range(1, 91)
    ]
    unallocated = [
        {"prize_position": index, "prize_name": "未分配😀" * 20, "count": 1}
        for index in range(2, 22)
    ]
    payload = {
        "campaign_name": "标题😀" * 20,
        "winners": winners,
        "undistributed": unallocated,
        "extended_tips": True,
    }
    original = deepcopy(payload)
    pages = draw_result_pages(payload)
    assert len(pages) > 1
    assert payload == original
    assert [item for page in pages for item in page["winners"]] == winners
    assert [item for page in pages for item in page["undistributed"]] == unallocated
    for index, page in enumerate(pages, start=1):
        text = draw_result_text(page)
        assert html_utf16_length(text) <= MESSAGE_UTF16_LIMIT
        assert f"第 {index}/{len(pages)} 页" in text
        assert "没有符合条件" not in text
        assert text.count('<a href="tg://user?id=') == len(page["winners"])


def test_single_page_and_empty_draw_do_not_show_spurious_pagination():
    payload = {"campaign_name": "测试", "winners": [], "undistributed": []}
    assert draw_result_pages(payload) == (payload,)
    assert "没有符合条件" in draw_result_text(payload)
    assert "第 1/1 页" not in draw_result_text(payload)


def test_covered_result_card_does_not_cut_mentions_or_overflow_with_long_emoji_names():
    payload = {
        "campaign_name": "标题😀" * 20,
        "has_cover": True,
        "card_state": "drawn",
        "winners": [
            _winner(index, username=None, display_name="😀" * 128) for index in range(1, 10)
        ],
    }
    text = _campaign_state_card_text(payload)
    assert html_utf16_length(text) <= CAPTION_UTF16_LIMIT
    assert "共 9 个中奖名额" in text
    assert "完整名单见群内结果公告" in text
    assert text.count('<a href="tg://user?id=') == text.count("</a>")
    assert text.count('<a href="tg://user?id=') >= 1
