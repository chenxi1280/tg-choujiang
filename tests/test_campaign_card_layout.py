from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram.types import InlineKeyboardMarkup

from tests.v30_helpers import FIRST, SECOND, rules_for
from tg_lottery.presentation.campaign_card import card_time
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.presentation.participation_rules import public_rule_blocks, rule_manifest
from tg_lottery.telegram.gateway import _campaign_card_text, _campaign_keyboard
from tg_lottery.telegram.member_activity_landing import show_legacy_rules


def card_payload(*, mode="message", bonuses=("activity", "invite")):
    return {
        "name": "秋日抽奖 <福利>",
        "campaign_ref": "abcdefgh",
        "bot_username": "lottery_test_bot",
        "timezone": "Asia/Shanghai",
        "cutoff_at": "2026-10-10T11:55:00+00:00",
        "draw_at": "2026-10-10T12:00:01+00:00",
        "rules": rules_for(mode, bonuses).model_dump(mode="json"),
        "prizes": [{"name": "礼物 & 奖励", "winner_count": 2}],
        "presentation": {
            "cover_file_id": "cover-file",
            "description": "1. 联系管理兑奖\n2. <说明> & 原样换行",
            "issuer_name": "发奖人 & 管理员",
            "participation_groups": [
                {"id": FIRST, "title": "一群", "join_url": "https://t.me/first_group"},
                {"id": SECOND, "title": "二群", "join_url": "https://t.me/second_group"},
            ],
            "required_chats": [
                {"id": -100125, "title": "公告频道", "type": "channel", "private": True},
            ],
        },
    }


def test_caption_keeps_group_gate_under_its_link_and_reference_section_order():
    payload = card_payload()
    text, pages = rule_manifest(payload)
    assert "一群</a>\n└ 🎟️ 单日有效发言满 <b>2</b> 条" in text
    assert "二群</a>\n└" not in text and "公告频道</a>\n└" not in text
    assert "大于" not in text and "每条至少" not in text
    sections = ("🎰", "📮", "🎁 <b>奖品内容", "⚠️", "🔋", "⏳", "📅", "🔗")
    assert [text.index(section) for section in sections] == sorted(
        text.index(section) for section in sections
    )
    assert "1. 联系管理兑奖\n2. &lt;说明&gt; &amp; 原样换行" in text
    assert "礼物 &amp; 奖励" in text and "发奖人 &amp; 管理员" in text
    assert "2026年10月10日 20时00分01秒" in text
    assert html_utf16_length(text) <= 900
    assert not pages
    rules_text = "\n".join(public_rule_blocks(payload))
    assert "转发及空白消息不计" in rules_text
    assert "图片、表情、贴纸及 GIF 表情均计数\uff0c不限制字数" in rules_text
    button = _campaign_keyboard(card_payload()).inline_keyboard[0][0]
    assert button.url in text and button.callback_data is None


def test_private_target_never_exposes_plain_invite_even_when_present():
    payload = card_payload()
    payload["presentation"]["required_chats"][0]["join_url"] = "https://t.me/+private-secret"
    text, _pages = rule_manifest(payload)
    assert "private-secret" not in text + "".join(public_rule_blocks(payload))
    assert (
        '关注-<a href="https://t.me/lottery_test_bot?start=condition_abcdefgh_3">公告频道</a>'
        in text
    )


def test_invitation_gate_and_disabled_bonuses_are_not_presented_as_speech_rules():
    payload = card_payload(mode="invite", bonuses=())
    text, _pages = rule_manifest(payload)
    assert "一群</a>\n└ 🎟️ 有效邀请满 <b>2</b> 人" in text
    assert "单日有效发言" not in text and "每日发言加成" not in text
    assert "人品加成\uff1a</b>未开启" in text
    assert "有效邀请要求" in "".join(public_rule_blocks(payload))


def test_caption_capacity_counts_visible_html_instead_of_link_attributes():
    payload = card_payload()
    long_url = "https://t.me/first_group?" + "x" * 900
    payload["presentation"]["participation_groups"][0]["join_url"] = long_url
    text, pages = rule_manifest(payload)
    assert len(text) > 1024 and html_utf16_length(text) < 900
    assert long_url in text and "正文较长" not in text
    assert not pages


def test_saved_time_is_converted_without_changing_the_instant():
    assert card_time("2026-10-10T12:00:01+00:00", "Europe/London") == (
        "2026年10月10日 20时00分01秒"
    )
    assert card_time("2026-10-10 13:00:01", "Europe/London") == ("2026年10月10日 20时00分01秒")


def test_legacy_global_bonus_rules_keep_actual_reward_and_daily_scope():
    payload = card_payload()
    payload["rules"] = {
        "bonus_types": ["activity", "invite"],
        "activity": {"reward_tickets": 3, "settle_once_per_day": True},
        "invite": {"reward_tickets": 4},
    }
    rules = "\n".join(public_rule_blocks(payload))
    assert "每个统计群每天有效发言满 10 条\uff0c全部达标 +3 次" in rules
    assert "每天奖励一次" in rules and "每个有效邀请 +4 次" in rules
    card = _campaign_card_text(payload)
    assert "人品加成\uff1a" in card and "邀请加成\uff1a" in card
    assert "按本场规则" in card and "不设上限" not in card


def test_long_prize_list_paginates_between_prizes_without_dropping_names():
    payload = card_payload()
    payload["prizes"] = [
        {"name": f"奖品{i}" + "完整名称" * 25, "winner_count": 1} for i in range(40)
    ]
    card, pages = rule_manifest(payload)
    assert "正文较长" in card and len(pages) >= 2
    assert all(html_utf16_length(page) <= 3500 for page in pages)
    assert all(prize["name"] in "\n".join(pages) for prize in payload["prizes"])


async def test_legacy_long_share_rules_keep_all_targets_and_cumulative_threshold():
    payload = card_payload()
    payload["rules"] = {"message_thresholds": {str(FIRST): 7}}
    payload["presentation"]["required_chats"] = [
        {"id": -100900 - i, "title": f"完整目标{i}" * 6, "join_url": f"https://t.me/target_{i}"}
        for i in range(100)
    ]
    card, pages = rule_manifest(payload)
    assert pages and "正文较长" in card and "rules_abcdefgh" in card
    campaign = SimpleNamespace(
        id=uuid4(),
        public_ref=payload["campaign_ref"],
        name=payload["name"],
        state="running",
        presentation=payload["presentation"],
        rules=payload["rules"],
        cutoff_at=datetime.fromisoformat(payload["cutoff_at"]),
        draw_at=datetime.fromisoformat(payload["draw_at"]),
        timezone=payload["timezone"],
    )
    flow = SimpleNamespace(
        _bot_username=payload["bot_username"],
        _queries=SimpleNamespace(
            prizes=AsyncMock(return_value=(SimpleNamespace(name="礼物", winner_count=2),))
        ),
        _condition_keyboard=lambda _: InlineKeyboardMarkup(inline_keyboard=[]),
    )
    message = SimpleNamespace(answer=AsyncMock())
    await show_legacy_rules(flow, message, campaign)
    full_rules = "\n".join(call.args[0] for call in message.answer.call_args_list)
    assert message.answer.await_count >= 2
    assert all(f"https://t.me/target_{i}" in full_rules for i in range(100))
    assert "累计发言至少 <b>7</b> 条" in full_rules
    assert all(call.kwargs["parse_mode"] == "HTML" for call in message.answer.call_args_list)
