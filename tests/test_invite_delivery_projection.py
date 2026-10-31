from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Chat, Message

from tests.helpers import seed_campaign
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.rules import CampaignRules, InviteRule
from tg_lottery.infrastructure.db import (
    AuditLog,
    Campaign,
    DrawRun,
    DrawSnapshot,
    InviteLink,
    TelegramOutbox,
)
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.gateway_requests import build_gateway_call, build_gateway_request
from tg_lottery.telegram.ui_style import plain_label


@pytest.mark.parametrize(
    ("phase", "expected", "active", "visible"),
    [
        ("active", "最晚计奖入群", True, True),
        ("deadline", "新入群不计加成", False, True),
        ("preheat", "邀请加成票尚未开始", False, True),
        ("settling", "本期已截止", False, True),
        ("drawn", "本场已开奖", False, False),
        ("cancelled", "本场已取消", False, False),
        ("revoked", "个人邀请链接已撤销", False, False),
        ("expired", "个人邀请链接已过期", False, False),
    ],
)
async def test_delayed_invite_delivery_uses_current_campaign_and_link_state(
    session_factory, *, phase, expected, active, visible
) -> None:
    outbox, campaign_id = await _queued_delivery(session_factory)
    original_payload = dict(outbox.payload)
    await _apply_phase(session_factory, campaign_id, outbox.payload["invite_link_id"], phase=phase)
    call = await build_gateway_call(session_factory, outbox)
    assert call.request.payload["invite_bonus_active"] is active
    assert call.request.payload["invite_entry_visible"] is visible
    assert expected in call.request.payload["invite_delivery_note"]
    assert outbox.payload == original_payload
    bot = Mock(send_message=AsyncMock(return_value=_delivered_message()))
    cipher = Mock()
    cipher.decrypt.return_value = "https://t.me/+persistent"
    result = await TelegramGateway(bot, cipher).execute(call.request, call.invite_link)
    assert result.remote_message_id == 77
    delivered = bot.send_message.await_args.args[1]
    buttons = [
        button
        for row in bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert expected in delivered
    if visible:
        assert "https://t.me/+persistent" in delivered
        assert plain_label(buttons[0].text) == ("复制邀请链接" if active else "复制入群链接")
        cipher.decrypt.assert_called_once()
    else:
        assert "https://t.me/+persistent" not in delivered
        assert not any(button.url or button.copy_text for button in buttons)
        assert "邀请记录" in [plain_label(button.text) for button in buttons]
        cipher.decrypt.assert_not_called()


async def test_invite_delivery_rejects_recipient_without_link_participant_identity(
    session_factory,
) -> None:
    outbox, _ = await _queued_delivery(session_factory)
    outbox.recipient_chat_id = 999
    with pytest.raises(ValueError, match="recipient does not match"):
        await build_gateway_request(session_factory, outbox)


async def _queued_delivery(session_factory) -> tuple[TelegramOutbox, object]:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session,
            rules=CampaignRules(invite=InviteRule(retention_hours=24)),
            cutoff_at=now + timedelta(hours=25),
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash="e" * 64,
            encrypted_url=b"ciphertext",
            telegram_name="queued_personal",
            expire_at=None,
        )
        session.add(link)
        await session.flush()
        outbox = TelegramOutbox(
            operation="send_invite_link",
            aggregate_type="invite_link",
            aggregate_id=str(link.id),
            recipient_chat_id=participant.user_id,
            business_key=f"send_invite_link:{link.id}:{participant.user_id}",
            request_hash="a" * 64,
            payload={
                "invite_link_id": str(link.id),
                "group_title": "测试群",
                "invite_bonus_active": True,
                "invite_entry_visible": True,
            },
            state=OutboxState.PENDING,
            run_at=now,
        )
        session.add(outbox)
        await session.flush()
        campaign_id = campaign.id
        session.expunge(outbox)
    return outbox, campaign_id


async def _apply_phase(session_factory, campaign_id, link_id, *, phase: str) -> None:
    from uuid import UUID

    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        link = await session.get(InviteLink, UUID(link_id))
        if phase == "deadline":
            campaign.cutoff_at = now + timedelta(hours=1)
        if phase == "preheat":
            campaign.state = CampaignState.SCHEDULED
            campaign.preheat_at = now + timedelta(hours=1)
            campaign.cutoff_at = now + timedelta(days=2)
        if phase == "settling":
            campaign.state = CampaignState.SETTLING
            campaign.cutoff_at = now - timedelta(minutes=1)
        if phase in {"drawn", "cancelled"}:
            campaign.state = CampaignState.DRAWN if phase == "drawn" else CampaignState.CANCELLED
            await _record_terminal_phase(session, campaign, phase=phase, now=now)
        if phase == "revoked":
            link.revoked_at = now
        if phase == "expired":
            link.expire_at = now - timedelta(minutes=1)


async def _record_terminal_phase(session, campaign, *, phase, now):
    if phase == "cancelled":
        session.add(
            AuditLog(
                actor_user_id=campaign.created_by,
                aggregate_type="campaign",
                aggregate_id=str(campaign.id),
                action="campaign.cancelled",
                created_at=now,
            )
        )
        return
    snapshot = DrawSnapshot(
        campaign_id=campaign.id,
        config_revision=campaign.config_revision,
        ledger_watermark=now,
        snapshot_hash="f" * 64,
        prize_plan_hash=campaign.prize_plan_hash,
        total_weight=0,
    )
    session.add(snapshot)
    await session.flush()
    session.add(
        DrawRun(
            snapshot_id=snapshot.id,
            algorithm_version="delivery-history-test",
            random_input="saved-random-input",
            completed_at=now,
        )
    )


def _delivered_message() -> Message:
    return Message(
        message_id=77, date=datetime.now(UTC), chat=Chat(id=201, type="private"), text="入群入口"
    )
