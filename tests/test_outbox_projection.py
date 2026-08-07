import asyncio
import base64
import os

from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState, OutboxState
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

    async def execute(self, outbox, invite_link=None):
        self.calls += 1
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


def _worker(session_factory, gateway: CountingGateway, cipher: SecretCipher) -> OutboxWorker:
    return OutboxWorker(
        owner_id="projection-worker",
        session_factory=session_factory,
        gateway=gateway,
        cipher=cipher,
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


async def _seed_failed_campaign_projection(session_factory):
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session, state=CampaignState.CANCELLED)
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
        payload={"campaign_name": "test", "prize_name": "prize"},
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
