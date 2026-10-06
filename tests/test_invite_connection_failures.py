import base64
import os
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import CreateChatInviteLink
from aiohttp import ClientConnectorError, ClientOSError
from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import DeliveryAttempt, TelegramOutbox
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.delivery_failures import failed_before_invite_request
from tg_lottery.telegram.outbox import OutboxWorker

CONNECTOR_DESCRIPTION = (
    "ClientConnectorError: Cannot connect to host api.telegram.org:443 "
    "ssl:default [Connection reset by peer]"
)


def network_error(kind: str) -> TelegramNetworkError:
    cause = {
        "connect": ClientConnectorError(
            SimpleNamespace(host="api.telegram.org", port=443, ssl=True),
            ConnectionResetError(104, "Connection reset by peer"),
        ),
        "response_reset": ClientOSError(104, "Connection reset by peer"),
        "timeout": TimeoutError("response timeout"),
        "text_only": None,
    }[kind]
    error = TelegramNetworkError(
        method=CreateChatInviteLink(chat_id=-100123), message=CONNECTOR_DESCRIPTION
    )
    error.__cause__ = cause
    return error


class FailingGateway:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls = 0

    async def execute(self, *_args):
        self.calls += 1
        raise network_error(self.kind)


@pytest.mark.parametrize("operation", ["create_invite_link", "create_required_chat_invite"])
def test_only_actual_connector_cause_proves_invite_was_not_sent(operation) -> None:
    assert failed_before_invite_request(operation, network_error("connect"))


@pytest.mark.parametrize("kind", ["response_reset", "timeout", "text_only"])
def test_error_text_does_not_replace_transport_evidence(kind) -> None:
    assert not failed_before_invite_request("create_invite_link", network_error(kind))


def test_compound_delivery_connection_error_cannot_disprove_an_earlier_write() -> None:
    assert not failed_before_invite_request("send_campaign_card", network_error("connect"))


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("connect", OutboxState.PRE_ACCEPT_FAILED),
        ("response_reset", OutboxState.UNKNOWN_HOLD),
        ("timeout", OutboxState.UNKNOWN_HOLD),
        ("text_only", OutboxState.UNKNOWN_HOLD),
    ],
)
async def test_worker_records_connection_phase_without_automatic_replay(
    session_factory, kind, expected
) -> None:
    row_id = await _seed_request(session_factory)
    gateway = FailingGateway(kind)
    worker = OutboxWorker(
        owner_id="connect-test",
        session_factory=session_factory,
        gateway=gateway,
        cipher=SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    assert await worker.process_once()
    assert not await worker.process_once()
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, row_id)
        attempt = await session.scalar(select(DeliveryAttempt))
        assert row.state is expected
        assert row.last_error == str(network_error(kind))
        assert attempt.certainty is (
            DeliveryCertainty.EXPLICIT_REJECTION
            if expected is OutboxState.PRE_ACCEPT_FAILED
            else DeliveryCertainty.UNKNOWN
        )
    assert gateway.calls == 1


async def _seed_request(sessions):
    async with sessions() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        payload = {
            "participant_id": str(participant.id),
            "campaign_id": str(campaign.id),
            "name": "lottery_connect_test",
            "persistent": True,
        }
        row = TelegramOutbox(
            operation="create_invite_link",
            aggregate_type="participant",
            aggregate_id=str(participant.id),
            recipient_chat_id=-100123,
            business_key=f"connection-test:{participant.id}",
            request_hash=canonical_hash(payload),
            payload=payload,
            state=OutboxState.PENDING,
            run_at=utcnow(),
        )
        session.add(row)
        await session.flush()
        return row.id
