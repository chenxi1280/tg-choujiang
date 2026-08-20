from typing import Any
from zoneinfo import ZoneInfoNotFoundError

import pytest
from aiogram.exceptions import TelegramBadRequest

from tests.helpers import seed_campaign
from tg_lottery.infrastructure.db import TelegramOutbox
from tg_lottery.telegram.gateway import (
    TelegramGateway,
    _campaign_card_text,
    _campaign_deep_link,
    _campaign_keyboard,
    _campaign_state_card_text,
    _draw_result_text,
)
from tg_lottery.telegram.gateway_requests import GatewayRequest, build_gateway_request

SNAPSHOT_HASH = "fc99b18147ccfaf6b7c3ab660c4ed6e688fc091ccaa97e4bdf0db477182702a7"


def test_campaign_card_localizes_internal_iso_times() -> None:
    text = _campaign_card_text(_campaign_payload())

    assert "📅 <b>开奖日期" in text
    assert "北京时间" in text
    assert "2026年08月08日 11时25分00秒" in text
    assert "T03:20:00" not in text
    assert "+00:00" not in text


def test_campaign_card_timezone_label_follows_campaign_timezone() -> None:
    payload = _campaign_payload() | {"timezone": "Asia/Kuala_Lumpur"}

    text = _campaign_card_text(payload)

    assert "马来西亚时间" in text
    assert "北京时间" not in text


def test_campaign_card_accepts_legacy_minute_payload() -> None:
    payload = _campaign_payload() | {
        "cutoff_at": "2026-08-08 11:20",
        "draw_at": "2026-08-08 11:25",
    }

    text = _campaign_card_text(payload)

    assert "2026年08月08日 11时25分00秒" in text
    assert "T03:25:00" not in text


def test_campaign_card_rejects_unknown_timezone() -> None:
    payload = _campaign_payload() | {"timezone": "Mars/Olympus"}

    with pytest.raises(ZoneInfoNotFoundError):
        _campaign_card_text(payload)


async def test_legacy_iso_payload_loads_persisted_campaign_timezone(session_factory) -> None:
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
        chat_id = group.chat_id
    payload = _campaign_payload()
    del payload["timezone"]
    outbox = TelegramOutbox(
        operation="send_campaign_card",
        aggregate_type="campaign",
        aggregate_id=str(campaign_id),
        recipient_chat_id=chat_id,
        payload=payload,
    )

    request = await build_gateway_request(session_factory, outbox)
    text = _campaign_card_text(request.payload)

    assert request.payload["timezone"] == "Asia/Shanghai"
    assert request.payload["campaign_ref"] == campaign.public_ref
    assert "timezone" not in outbox.payload
    assert "2026年08月08日 11时25分00秒" in text
    assert "UTC" not in text


def test_campaign_deep_link_only_uses_the_public_reference() -> None:
    internal_id = "fc99b181-47cc-faf6-b7c3-ab660c4ed6e6"
    payload = {
        "bot_username": "lottery_test_bot",
        "campaign_id": internal_id,
        "campaign_ref": "AbCdEfGhIjKlMnOpQrSt-_",
    }

    link = _campaign_deep_link(payload, "join")

    assert link.endswith("?start=join_AbCdEfGhIjKlMnOpQrSt-_")
    assert internal_id not in link


def test_campaign_card_separates_rules_registration_and_join_links() -> None:
    payload = _campaign_payload() | {
        "bot_username": "lottery_test_bot",
        "campaign_ref": "AbCdEfGhIjKlMnOpQrSt-_",
        "presentation": {
            "required_chats": [
                {"id": -1001, "title": "公开群", "join_url": "https://t.me/public_group"},
                {"id": -1002, "title": "私有群", "private": True},
            ]
        },
    }

    keyboard = _campaign_keyboard(payload)
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    urls = [b.url for row in keyboard.inline_keyboard for b in row if b.url]

    assert "member:join:AbCdEfGhIjKlMnOpQrSt-_" in datas
    assert "member:rules:AbCdEfGhIjKlMnOpQrSt-_" not in datas
    assert "https://t.me/lottery_test_bot?start=rules_AbCdEfGhIjKlMnOpQrSt-_" in urls
    assert urls == [
        "https://t.me/lottery_test_bot?start=rules_AbCdEfGhIjKlMnOpQrSt-_",
        "https://t.me/lottery_test_bot?start=join_AbCdEfGhIjKlMnOpQrSt-_",
    ]


def test_campaign_card_explains_per_group_messages_and_channel_following() -> None:
    payload = _campaign_payload() | {
        "rules": {
            "draw_mode": "fixed",
            "bonus_types": ["activity"],
            "message_thresholds": {"-1001": 10, "-1002": 10, "-1003": 10},
            "activity": {"message_threshold": 10, "reward_tickets": 2},
        },
        "presentation": {
            "main_chat": {"id": -1001, "title": "主活动群", "type": "supergroup"},
            "required_chats": [
                {"id": -1002, "title": "协作群", "type": "supergroup"},
                {"id": -1003, "title": "公告频道", "type": "channel"},
            ],
        },
    }

    text = _campaign_card_text(payload)

    assert "加入-主活动群" in text
    assert "加入-协作群" in text
    assert "关注-公告频道" in text
    assert text.count("入口见下方按钮") == 3
    assert text.count("发言数大于 <b>10</b> 条") == 2
    assert "🔋 <b>抽奖加成" in text
    assert "每日发言满10条+2次" in text


def test_draw_result_keeps_internal_hash_out_of_public_message() -> None:
    text = _draw_result_text(
        {
            "campaign_name": "123",
            "snapshot_hash": SNAPSHOT_HASH,
            "commitment_hash": "a" * 64,
            "revealed_seed": "b" * 64,
            "campaign_id": "060fda6f-280e-4012-8e2a-32dda7b73580",
            "winners": [],
            "undistributed": [{"prize_position": 1, "prize_name": "一等奖", "count": 1}],
            "extended_tips": True,
        }
    )

    assert "本期没有符合条件的中奖候选人。" in text
    assert "未分配名额:" in text
    assert "冻结的报名资格和抽奖次数权重" in text
    assert "快照:" not in text
    assert "承诺哈希" not in text
    assert "随机种子" not in text
    assert SNAPSHOT_HASH not in text


async def test_delete_message_operation_calls_telegram_with_frozen_target() -> None:
    class DeleteBot:
        async def delete_message(self, chat_id, message_id):
            self.target = (chat_id, message_id)
            return True

    bot = DeleteBot()
    gateway = TelegramGateway(bot, object())

    result = await gateway.execute(GatewayRequest("delete_message", 202, {"message_id": 77}))

    assert bot.target == (202, 77)
    assert result.remote_result == {"ok": True}


async def test_personal_invite_link_remains_joinable_after_campaign_cutoff() -> None:
    class InviteBot:
        async def create_chat_invite_link(self, **values):
            self.values = values
            return type(
                "Link",
                (),
                {
                    "invite_link": "https://t.me/+personal",
                    "model_dump": lambda self, **_: {"invite_link": self.invite_link},
                },
            )()

    bot = InviteBot()
    gateway = TelegramGateway(bot, object())
    payload = {
        "name": "lottery_personal",
        "expire_at": "2026-08-08T03:20:00+00:00",
        "persistent": True,
    }

    await gateway.execute(GatewayRequest("create_invite_link", -1001, payload))

    assert bot.values["expire_date"] is None


def test_campaign_state_card_has_business_text_only() -> None:
    settling = _campaign_state_card_text({"campaign_name": "周末抽奖", "card_state": "settling"})
    drawn = _campaign_state_card_text({"campaign_name": "周末抽奖", "card_state": "drawn"})

    assert settling.startswith("🎰 <b>周末抽奖</b>")
    assert "报名已截止" in settling
    assert "已经开奖" in drawn
    assert "SETTLING" not in settling
    assert "中奖者" not in drawn


def test_campaign_card_escapes_ampersand_in_join_url() -> None:
    payload = _lotterybest_card_payload()
    payload["presentation"]["required_chats"][0]["join_url"] = "https://t.me/welfare?x=1&y=2"

    text = _campaign_card_text(payload)

    assert 'href="https://t.me/welfare?x=1&amp;y=2"' in text
    assert 'href="https://t.me/welfare?x=1&y=2"' not in text


def test_drawn_state_card_lists_winner_names() -> None:
    text = _campaign_state_card_text(
        {
            "campaign_name": "周末抽奖",
            "card_state": "drawn",
            "winners": [
                {"display_name": "张三", "prize_name": "一等奖"},
                {"display_name": "李四", "prize_name": "二等奖"},
            ],
        }
    )

    assert "🏆 中奖者" in text
    assert "张三" in text
    assert "李四" in text
    assert "等共" not in text


def test_drawn_state_card_caps_winner_list_at_eight_rows() -> None:
    names = [f"成员{index}" for index in range(1, 11)]
    text = _campaign_state_card_text(
        {
            "campaign_name": "周末抽奖",
            "card_state": "drawn",
            "winners": [{"display_name": name} for name in names],
        }
    )

    assert "成员8" in text
    assert "成员9" not in text
    assert "成员10" not in text
    assert "等共 10 人" in text
    assert "完整名单见群内结果公告" in text


def test_drawn_state_card_escapes_winner_names() -> None:
    text = _campaign_state_card_text(
        {
            "campaign_name": "周末抽奖",
            "card_state": "drawn",
            "winners": [{"display_name": "<b>伪造者</b>"}],
        }
    )

    assert "&lt;b&gt;伪造者&lt;/b&gt;" in text
    assert "<b>伪造者</b>" not in text


class _FakeSentMessage:
    message_id = 909

    def model_dump(self, **_: object) -> dict[str, object]:
        return {"message_id": self.message_id}


class FakeCardBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, tuple[object, ...], dict[str, Any]]] = []
        self.pinned: list[tuple[object, object, dict[str, Any]]] = []

    async def send_message(
        self, chat_id: object, text: object, **kwargs: object
    ) -> _FakeSentMessage:
        self.sent.append(("send_message", (chat_id, text), kwargs))
        return _FakeSentMessage()

    async def send_photo(
        self, chat_id: object, photo: object, **kwargs: object
    ) -> _FakeSentMessage:
        self.sent.append(("send_photo", (chat_id, photo), kwargs))
        return _FakeSentMessage()

    async def pin_chat_message(self, chat_id: object, message_id: object, **kwargs: object) -> bool:
        self.pinned.append((chat_id, message_id, kwargs))
        return True


class PinForbiddenCardBot(FakeCardBot):
    async def pin_chat_message(self, chat_id: object, message_id: object, **kwargs: object) -> bool:
        raise TelegramBadRequest(method="pinChatMessage", message="not enough rights")


def _lotterybest_card_payload() -> dict[str, object]:
    return {
        "name": "周末抽奖 <福利场>",
        "total_winners": 2,
        "prizes": [{"position": 1, "name": "一等奖", "winner_count": 2}],
        "cutoff_at": "2026-08-08T03:20:00+00:00",
        "draw_at": "2026-08-08T03:25:00+00:00",
        "timezone": "Asia/Shanghai",
        "bot_username": "lottery_test_bot",
        "campaign_ref": "AbCdEfGhIjKlMnOpQrSt-_",
        "presentation": {
            "required_chats": [
                {
                    "id": -100123,
                    "title": "福利群",
                    "type": "supergroup",
                    "join_url": "https://t.me/welfare_group",
                },
            ],
        },
        "rules": {
            "draw_mode": "fixed",
            "bonus_types": ["invite", "activity"],
            "allowed_regions": ["广东"],
            "username_required": True,
            "message_thresholds": {"-100123": 10},
            "activity": {"message_threshold": 10, "reward_tickets": 2},
        },
    }


async def test_campaign_card_matches_lotterybest_format() -> None:
    bot = FakeCardBot()
    gateway = TelegramGateway(bot, object())

    await gateway.execute(GatewayRequest("send_campaign_card", -1001, _lotterybest_card_payload()))

    operation, _, kwargs = bot.sent[0]
    text = bot.sent[0][1][1]
    assert operation == "send_message"
    assert "🎰 <b>周末抽奖 &lt;福利场&gt;</b>" in text
    assert "📮 <b>参与群与准入条件" in text
    assert "🌏️ 归属地" in text
    assert "广东" in text
    assert "🪪 必须设置用户名" in text
    assert '🎫 加入-<a href="https://t.me/welfare_group">福利群</a>' in text
    assert "发言数大于 <b>10</b> 条" in text
    assert "🎁 <b>奖品内容" in text
    assert "💰️ 一等奖" in text
    assert "<b>2</b>" in text
    assert "🔋 <b>抽奖加成" in text
    assert "每日发言满10条+2次" in text
    assert "📅 <b>开奖日期" in text
    assert "2026年08月08日 11时25分00秒" in text
    assert kwargs["parse_mode"] == "HTML"
    assert bot.pinned[0][:2] == (-1001, _FakeSentMessage.message_id)
    assert bot.pinned[0][2]["disable_notification"] is True


async def test_campaign_keyboard_uses_private_rules_link() -> None:
    bot = FakeCardBot()
    gateway = TelegramGateway(bot, object())

    await gateway.execute(GatewayRequest("send_campaign_card", -1001, _lotterybest_card_payload()))

    keyboard = bot.sent[0][2]["reply_markup"]
    buttons = [row[0] for row in keyboard.inline_keyboard]

    assert len(buttons) == 4
    assert buttons[0].callback_data.startswith("member:join:")
    assert buttons[1].callback_data.startswith("member:bonus:")
    assert "?start=rules_" in buttons[2].url
    assert "?start=join_" in buttons[3].url
    assert buttons[2].callback_data is None
    assert buttons[3].callback_data is None


async def test_pin_failure_does_not_fail_delivery() -> None:
    bot = PinForbiddenCardBot()
    gateway = TelegramGateway(bot, object())

    result = await gateway.execute(
        GatewayRequest("send_campaign_card", -1001, _lotterybest_card_payload())
    )

    assert len(bot.sent) == 1
    assert result.remote_message_id == _FakeSentMessage.message_id
    assert result.remote_result == {"message_id": _FakeSentMessage.message_id}


def _campaign_payload() -> dict[str, object]:
    return {
        "name": "123",
        "total_winners": 1,
        "prizes": [{"position": 1, "name": "一等奖", "winner_count": 1}],
        "cutoff_at": "2026-08-08T03:20:00+00:00",
        "draw_at": "2026-08-08T03:25:00+00:00",
        "timezone": "Asia/Shanghai",
        "presentation": {},
        "rules": {"draw_mode": "fixed", "luck_mode": "off"},
    }
