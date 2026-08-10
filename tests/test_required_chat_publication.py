import base64
import os

from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.records import utcnow
from tg_lottery.application.required_chat_links import decrypt_required_chat_url
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway import GatewayResult
from tg_lottery.telegram.outbox import OutboxWorker

PRIVATE_URL = "https://t.me/+required-private-value"


class RequiredInviteGateway:
    async def execute(self, request, invite_link=None):
        assert request.operation == "create_required_chat_invite"
        return GatewayResult(remote_result={"ok": True}, invite_link=PRIVATE_URL)


def _cipher() -> SecretCipher:
    return SecretCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())


async def _seed_private_publication(session_factory):
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(session, state=CampaignState.PUBLISHING)
        campaign.presentation = {
            "main_chat": {
                "id": group.chat_id,
                "title": "私有主群",
                "type": "supergroup",
                "username": None,
                "private": True,
                "join_url": None,
            }
        }
        payload = {
            "campaign_id": str(campaign.id),
            "chat_id": group.chat_id,
            "name": "lottery_required_test",
            "expire_at": campaign.cutoff_at.isoformat(),
            "bot_username": "lottery_test_bot",
        }
        row = TelegramOutbox(
            operation="create_required_chat_invite",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=group.chat_id,
            business_key=f"required_chat_invite:{campaign.id}:{group.chat_id}",
            request_hash=canonical_hash(payload),
            payload=payload,
            state=OutboxState.PENDING,
            run_at=utcnow(),
        )
        session.add(row)
        return campaign.id


async def test_private_required_chat_is_confirmed_before_campaign_card(session_factory) -> None:
    cipher = _cipher()
    campaign_id = await _seed_private_publication(session_factory)

    worker = OutboxWorker(
        owner_id="required-link-worker",
        session_factory=session_factory,
        gateway=RequiredInviteGateway(),
        cipher=cipher,
    )
    assert await worker.process_once() is True

    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        card = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_campaign_card")
        )
        chat = campaign.presentation["main_chat"]
        assert PRIVATE_URL not in str(campaign.presentation)
        assert decrypt_required_chat_url(cipher, campaign.id, chat) == PRIVATE_URL
        assert card is not None and card.state is OutboxState.PENDING
        assert card.payload["campaign_ref"] == campaign.public_ref
