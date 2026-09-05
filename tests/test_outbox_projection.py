import asyncio
import base64
import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

import tg_lottery.telegram.outbox as outbox_module
from tests.helpers import seed_campaign
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import (
    Campaign,
    DeliveryAttempt,
    InviteLink,
    TelegramOutbox,
)
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.outbox import OutboxWorker

INVITE_URL = "https://t.me/+private-invite-value"


class CountingGateway:
    def __init__(self, result: GatewayResult) -> None:
        self.result = result
        self.calls = 0
        self.requests = []

    async def execute(self, outbox, invite_link=None):
        self.calls += 1
        self.requests.append(outbox)
        return self.result


class BlockingGateway:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, outbox, invite_link=None):
        self.started.set()
        await self.release.wait()
        return GatewayResult(remote_result={"ok": True}, remote_message_id=92)


def _cipher() -> SecretCipher:
    return SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())


def _worker(
    session_factory,
    gateway: CountingGateway,
    cipher: SecretCipher,
    *,
    invite_name_factory=None,
) -> OutboxWorker:
    options = {"invite_name_factory": invite_name_factory} if invite_name_factory else {}
    return OutboxWorker(
        owner_id="projection-worker",
        session_factory=session_factory,
        gateway=gateway,
        cipher=cipher,
        **options,
    )


async def test_confirmed_delivery_is_not_lost_or_resent_when_projection_fails(
    session_factory,
) -> None:
    outbox_id, campaign_id = await _seed_failed_campaign_projection(session_factory)
    gateway = CountingGateway(GatewayResult(remote_result={"ok": True}, remote_message_id=91))
    worker = _worker(session_factory, gateway, _cipher())
    assert await worker.process_once() is True
    await _assert_confirmation_and_repair_state(session_factory, outbox_id, campaign_id)
    assert await worker.process_once() is True
    await _assert_projection_recovered(session_factory, outbox_id, campaign_id, gateway=gateway)


async def test_local_projection_failure_does_not_mark_gateway_started(
    session_factory,
    monkeypatch,
) -> None:
    outbox_id, _ = await _seed_failed_campaign_projection(session_factory)
    gateway = CountingGateway(GatewayResult(remote_result={"ok": True}, remote_message_id=90))

    async def fail_projection(*_args):
        raise RuntimeError("local campaign projection unavailable")

    monkeypatch.setattr(outbox_module, "build_gateway_call", fail_projection)

    with pytest.raises(RuntimeError, match="projection unavailable"):
        await _worker(session_factory, gateway, _cipher()).process_once()

    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        assert stored.state is OutboxState.CLAIMED
        assert stored.gateway_started_at is None
    assert gateway.calls == 0


async def test_invalid_local_projection_fails_before_gateway(session_factory, monkeypatch) -> None:
    outbox_id, _ = await _seed_failed_campaign_projection(session_factory)
    gateway = CountingGateway(GatewayResult(remote_result={"ok": True}, remote_message_id=90))

    async def reject_projection(*_args):
        raise ValueError("invalid local campaign projection")

    monkeypatch.setattr(outbox_module, "build_gateway_call", reject_projection)

    assert await _worker(session_factory, gateway, _cipher()).process_once() is True

    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        attempts = await session.scalar(select(func.count(DeliveryAttempt.id)))
        assert stored.state is OutboxState.PRE_ACCEPT_FAILED
        assert stored.gateway_started_at is None
        assert attempts == 1
    assert gateway.calls == 0


async def _seed_failed_campaign_projection(session_factory):
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session, state=CampaignState.DRAFT)
        row = TelegramOutbox(
            operation="send_campaign_card",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=group.chat_id,
            business_key="projection-retry",
            request_hash="e" * 64,
            payload={},
            state=OutboxState.PENDING,
            run_at=utcnow(),
        )
        session.add(row)
        await session.flush()
        return row.id, campaign.id


async def _assert_confirmation_and_repair_state(session_factory, outbox_id, campaign_id) -> None:
    async with session_factory() as session, session.begin():
        stored = await session.get(TelegramOutbox, outbox_id, with_for_update=True)
        campaign = await session.get(Campaign, campaign_id, with_for_update=True)
        assert stored.state is OutboxState.CONFIRMED
        assert stored.projected_at is None
        assert stored.last_error.startswith("projection:")
        campaign.state = CampaignState.PUBLISHING
        stored.run_at = utcnow()


async def _assert_projection_recovered(session_factory, outbox_id, campaign_id, *, gateway) -> None:
    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        campaign = await session.get(Campaign, campaign_id)
        attempts = await session.scalar(select(func.count(DeliveryAttempt.id)))
        assert stored.projected_at is not None
        assert campaign.published_message_id == 91
        assert gateway.calls == 1
        assert attempts == 1


async def test_invite_link_remote_fact_is_sanitized_and_encrypted(session_factory) -> None:
    cipher = _cipher()
    outbox_id = await _seed_invite_outbox(session_factory)
    gateway = CountingGateway(
        GatewayResult(
            remote_result={"invite_link": INVITE_URL, "name": "lot_secure"},
            invite_link=INVITE_URL,
        )
    )
    assert await _worker(session_factory, gateway, cipher).process_once() is True
    await _assert_invite_secrets(session_factory, outbox_id, cipher)


async def test_legacy_pending_invite_name_is_replaced_before_gateway(session_factory) -> None:
    outbox_id, participant_id = await _seed_legacy_invite_outbox(session_factory)
    gateway = CountingGateway(GatewayResult(remote_result={"ok": True}, invite_link=INVITE_URL))
    worker = _worker(
        session_factory,
        gateway,
        _cipher(),
        invite_name_factory=lambda: "lottery_repaired_once",
    )

    assert await worker.process_once() is True

    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        link = await session.scalar(select(InviteLink))
        assert stored.payload["name"] == "lottery_repaired_once"
        assert stored.request_hash == canonical_hash(stored.payload)
        assert link.telegram_name == "lottery_repaired_once"
    assert gateway.requests[0].payload["name"] == "lottery_repaired_once"
    assert participant_id.hex[:20] not in gateway.requests[0].payload["name"]


async def test_legacy_invite_after_gateway_start_is_held_without_rewriting(
    session_factory,
) -> None:
    outbox_id, _ = await _seed_legacy_invite_outbox(session_factory)
    async with session_factory() as session, session.begin():
        row = await session.get(TelegramOutbox, outbox_id, with_for_update=True)
        legacy_name = row.payload["name"]
        row.state = OutboxState.CLAIMED
        row.lease_owner = "lost-worker"
        row.lease_until = utcnow() - timedelta(minutes=1)
        row.gateway_started_at = utcnow() - timedelta(minutes=2)
    gateway = CountingGateway(GatewayResult(remote_result={"ok": True}, invite_link=INVITE_URL))

    assert await _worker(session_factory, gateway, _cipher()).process_once() is False

    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        assert stored.state is OutboxState.UNKNOWN_HOLD
        assert stored.payload["name"] == legacy_name
    assert gateway.calls == 0


async def test_running_outbox_delivery_renews_lease(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add(_winner_notice_outbox())
    gateway = BlockingGateway()
    active = _short_lease_outbox("active", gateway, session_factory)
    recovery = _short_lease_outbox("recovery", CountingGateway(GatewayResult({})), session_factory)
    delivery = asyncio.create_task(active.process_once())
    await gateway.started.wait()
    await asyncio.sleep(1.2)
    assert await recovery.process_once() is False
    gateway.release.set()
    assert await delivery is True
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
        assert row.state is OutboxState.CONFIRMED
        assert row.remote_message_id == 92


async def test_late_campaign_card_confirmation_queues_cancellation_edit(
    session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session, state=CampaignState.CANCELLED)
        row = TelegramOutbox(
            operation="send_campaign_card",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=group.chat_id,
            business_key="late-cancelled-card",
            request_hash="2" * 64,
            payload={},
            state=OutboxState.PENDING,
            run_at=utcnow(),
        )
        session.add(row)
        await session.flush()
        original_id = row.id
    gateway = CountingGateway(GatewayResult(remote_result={"ok": True}, remote_message_id=93))
    assert await _worker(session_factory, gateway, _cipher()).process_once() is True
    async with session_factory() as session:
        original = await session.get(TelegramOutbox, original_id)
        compensation = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "edit_campaign_card_cancelled")
        )
        assert original.projected_at is not None
        assert compensation.payload["message_id"] == 93
        assert compensation.state is OutboxState.PENDING


def _short_lease_outbox(owner, gateway, session_factory) -> OutboxWorker:
    return OutboxWorker(
        owner_id=owner,
        session_factory=session_factory,
        gateway=gateway,
        cipher=_cipher(),
        lease_seconds=1,
        renew_interval_seconds=0.2,
    )


def _winner_notice_outbox() -> TelegramOutbox:
    return TelegramOutbox(
        operation="send_winner_notice",
        aggregate_type="winner",
        aggregate_id="winner-renewal",
        recipient_chat_id=201,
        business_key="winner-renewal",
        request_hash="1" * 64,
        payload={"campaign_name": "test", "prize_name": "prize", "issuer_user_id": 101},
        state=OutboxState.PENDING,
        run_at=utcnow(),
    )


async def _seed_invite_outbox(session_factory):
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session)
        row = TelegramOutbox(
            operation="create_invite_link",
            aggregate_type="participant",
            aggregate_id=str(participant.id),
            recipient_chat_id=group.chat_id,
            business_key="secure-invite-result",
            request_hash="f" * 64,
            payload={
                "campaign_id": str(campaign.id),
                "participant_id": str(participant.id),
                "name": "lot_secure",
                "expire_at": campaign.cutoff_at.isoformat(),
            },
            state=OutboxState.PENDING,
            run_at=utcnow(),
        )
        session.add(row)
        await session.flush()
        return row.id


async def _seed_legacy_invite_outbox(session_factory):
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(session)
        payload = {
            "campaign_id": str(campaign.id),
            "participant_id": str(participant.id),
            "name": f"lot_{participant.id.hex[:20]}",
            "expire_at": campaign.cutoff_at.isoformat(),
        }
        row = TelegramOutbox(
            operation="create_invite_link",
            aggregate_type="participant",
            aggregate_id=str(participant.id),
            recipient_chat_id=group.chat_id,
            business_key="legacy-invite-name",
            request_hash=canonical_hash(payload),
            payload=payload,
            state=OutboxState.PENDING,
            run_at=utcnow(),
        )
        session.add(row)
        await session.flush()
        return row.id, participant.id


async def _assert_invite_secrets(session_factory, outbox_id, cipher: SecretCipher) -> None:
    async with session_factory() as session:
        stored = await session.get(TelegramOutbox, outbox_id)
        attempt = await session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == outbox_id)
        )
        link = await session.scalar(select(InviteLink))
        assert "invite_link" not in attempt.remote_result
        assert INVITE_URL not in str(attempt.remote_result)
        assert stored.encrypted_remote_secret is None
        assert stored.projected_at is not None
        assert cipher.decrypt(link.encrypted_url, context=f"invite-link:{link.id}") == INVITE_URL
