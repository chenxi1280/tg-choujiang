import pytest

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

    assert "报名截止: 2026年08月08日 11:20 (北京时间)" in text
    assert "开奖时间: 2026年08月08日 11:25 (北京时间)" in text
    assert "T03:20:00" not in text
    assert "+00:00" not in text


def test_campaign_card_accepts_legacy_minute_payload() -> None:
    payload = _campaign_payload() | {
        "cutoff_at": "2026-08-08 11:20",
        "draw_at": "2026-08-08 11:25",
    }

    text = _campaign_card_text(payload)

    assert "报名截止: 2026年08月08日 11:20 (北京时间)" in text
    assert "开奖时间: 2026年08月08日 11:25 (北京时间)" in text


def test_campaign_card_rejects_unknown_timezone() -> None:
    payload = _campaign_payload() | {"timezone": "Mars/Olympus"}

    with pytest.raises(ValueError, match="unsupported public timezone"):
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
    assert "报名截止: 2026年08月08日 11:20 (北京时间)" in text
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
                {"title": "公开群", "join_url": "https://t.me/public_group"},
                {"title": "私有群", "private": True},
            ]
        },
    }

    keyboard = _campaign_keyboard(payload)
    urls = [row[0].url for row in keyboard.inline_keyboard]

    assert urls[0].endswith("?start=join_AbCdEfGhIjKlMnOpQrSt-_")
    assert urls[1].endswith("?start=rules_AbCdEfGhIjKlMnOpQrSt-_")
    assert urls[2] == "https://t.me/public_group"
    assert urls[3].endswith("?start=condition_AbCdEfGhIjKlMnOpQrSt-__2")


def test_draw_result_keeps_internal_hash_out_of_public_message() -> None:
    text = _draw_result_text(
        {
            "campaign_name": "123",
            "snapshot_hash": SNAPSHOT_HASH,
            "winners": [],
            "undistributed": [{"prize_position": 1, "prize_name": "一等奖", "count": 1}],
            "extended_tips": True,
        }
    )

    assert "本期没有符合条件的中奖候选人。" in text
    assert "未分配名额:" in text
    assert "冻结的报名资格和抽奖票" in text
    assert "快照:" not in text
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


def test_campaign_state_card_has_business_text_only() -> None:
    settling = _campaign_state_card_text({"campaign_name": "周末抽奖", "card_state": "settling"})
    drawn = _campaign_state_card_text({"campaign_name": "周末抽奖", "card_state": "drawn"})

    assert "报名已截止" in settling
    assert "已经开奖" in drawn
    assert "SETTLING" not in settling


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
