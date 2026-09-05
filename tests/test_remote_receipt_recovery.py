from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from sqlalchemy import select

from tests.test_outbox_projection import _cipher, _seed_failed_campaign_projection, _worker
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import (
    AuditLog,
    Campaign,
    DeliveryAttempt,
    TelegramOutbox,
    TelegramUpdate,
)
from tg_lottery.ops.reconcile_pinned_cards import reconcile_pinned_card
from tg_lottery.telegram.remote_serialization import remote_object

BOT_ID = 9001
SOURCE_UPDATE_ID = 501
CARD_MESSAGE_ID = 91


async def test_value_error_after_gateway_start_is_unknown_and_never_replayed(session_factory):
    outbox_id, _ = await _seed_failed_campaign_projection(session_factory)

    class Gateway:
        calls = 0

        async def execute(self, request, invite_link):
            self.calls += 1
            raise ValueError("response serialization failed after successful send")

    gateway = Gateway()
    worker = _worker(session_factory, gateway, _cipher())
    await worker.process_once()
    await worker.process_once()
    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        attempt = await session.scalar(select(DeliveryAttempt))
        assert stored.state is OutboxState.UNKNOWN_HOLD
        assert stored.gateway_started_at is not None
        assert attempt.certainty is DeliveryCertainty.UNKNOWN
    assert gateway.calls == 1


async def _seed_receipt_failure(sessions):
    outbox_id, campaign_id = await _seed_failed_campaign_projection(sessions)
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        row = await session.get(TelegramOutbox, outbox_id)
        campaign = await session.get(Campaign, campaign_id)
        row.state = OutboxState.PRE_ACCEPT_FAILED
        row.gateway_started_at = now - timedelta(seconds=1)
        message = Message(
            message_id=CARD_MESSAGE_ID,
            date=now,
            chat=Chat(id=row.recipient_chat_id, type="supergroup"),
            from_user=User(id=BOT_ID, is_bot=True, first_name="测试机器人"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="立即参与", callback_data=f"member:join:{campaign.public_ref}"
                        )
                    ]
                ]
            ),
        )
        payload = {"message": {"pinned_message": remote_object(message)}}
        session.add(
            TelegramUpdate(
                bot_id=BOT_ID,
                update_id=SOURCE_UPDATE_ID,
                update_type="message",
                routing_chat_id=row.recipient_chat_id,
                event_at=now,
                payload=payload,
                payload_hash=canonical_hash(payload),
            )
        )
        session.add(
            DeliveryAttempt(
                outbox_id=row.id,
                attempt_number=1,
                telegram_method="send_campaign_card",
                certainty=DeliveryCertainty.EXPLICIT_REJECTION,
                error_code="PydanticSerializationError",
                finished_at=now,
            )
        )
    return outbox_id


async def _reconcile(sessions, outbox_id, **kwargs):
    return await reconcile_pinned_card(
        sessions,
        outbox_id=outbox_id,
        source_update_id=SOURCE_UPDATE_ID,
        bot_id=BOT_ID,
        actor_user_id=101,
        **kwargs,
    )


async def test_pinned_receipt_reconciliation_preserves_original_attempt_and_audit(session_factory):
    outbox_id = await _seed_receipt_failure(session_factory)
    preview = await _reconcile(session_factory, outbox_id)
    assert preview["applied"] is False
    with pytest.raises(ValueError, match="preview changed"):
        await _reconcile(session_factory, outbox_id, expected_fingerprint="stale")
    result = await _reconcile(
        session_factory, outbox_id, expected_fingerprint=preview["fingerprint"]
    )
    assert result["message_id"] == CARD_MESSAGE_ID
    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        attempts = list(
            await session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number))
        )
        audit = await session.scalar(select(AuditLog))
        assert stored.state is OutboxState.CONFIRMED
        assert stored.remote_message_id == CARD_MESSAGE_ID
        assert attempts[0].error_code == "PydanticSerializationError"
        assert attempts[1].certainty is DeliveryCertainty.CONFIRMED
        assert audit.details["source_update_id"] == SOURCE_UPDATE_ID


@pytest.mark.parametrize("field", ["sender", "chat", "campaign"])
async def test_pinned_receipt_reconciliation_rejects_unrelated_evidence(session_factory, field):
    outbox_id = await _seed_receipt_failure(session_factory)
    async with session_factory() as session, session.begin():
        update = await session.scalar(select(TelegramUpdate))
        pinned = dict(update.payload["message"]["pinned_message"])
        if field == "sender":
            pinned["from_user"] = pinned["from_user"] | {"id": 201}
        elif field == "chat":
            pinned["chat"] = pinned["chat"] | {"id": -999}
        else:
            pinned["reply_markup"] = {
                "inline_keyboard": [[{"text": "参与", "callback_data": "member:join:other"}]]
            }
        update.payload = {"message": {"pinned_message": pinned}}
    with pytest.raises(ValueError):
        await _reconcile(session_factory, outbox_id)
    async with session_factory() as session:
        assert (await session.get(TelegramOutbox, outbox_id)).state is OutboxState.PRE_ACCEPT_FAILED
