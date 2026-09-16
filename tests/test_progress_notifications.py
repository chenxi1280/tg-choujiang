from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select

from tests.test_base_activity_integration import messages, seed
from tests.test_invitation_bonus_concurrency import _seed_invitations
from tests.test_telegram_gateway_messages import FakeCardBot
from tg_lottery.application.activity import ActivityService
from tg_lottery.application.base_activity_notifications import notify_unqualified_at_cutoff
from tg_lottery.application.invitation_rewards import add_invite_bonus
from tg_lottery.infrastructure.db import Campaign, Invitation, Participant, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.gateway_requests import GatewayRequest


async def test_progress_gateway_mentions_identity_escapes_text_and_enables_alert():
    bot = FakeCardBot()
    request = GatewayRequest(
        "send_progress_notice",
        -100123,
        {
            "user_id": 201,
            "display_name": "<冒名用户>",
            "campaign_name": "<活动>",
            "campaign_ref": "testref",
            "text": "完成 +1",
            "ticket_status": "暂存 1 次",
        },
    )
    result = await TelegramGateway(bot, object()).execute(request)
    text = bot.sent[0][1][1]
    assert 'href="tg://user?id=201">@&lt;冒名用户&gt;</a>' in text
    assert "&lt;活动&gt;" in text
    assert bot.sent[0][2]["disable_notification"] is False
    assert (
        bot.sent[0][2]["reply_markup"].inline_keyboard[0][0].callback_data
        == "member:tickets:testref"
    )
    assert result.remote_message_id is not None


async def test_invitation_rewards_notify_with_stashed_balance(session_factory):
    campaign_id, participant_id, invitation_ids = await _seed_invitations(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        participant = await session.get(Participant, participant_id)
        participant.base_qualified = False
        participant.ticket_total_cache = 1
        invitation = await session.get(Invitation, invitation_ids[0])
        await add_invite_bonus(
            session,
            campaign=campaign,
            invitation=invitation,
            reward=2,
            business_key="notice-award",
            occurred_at=datetime.now(UTC),
        )
    async with session_factory() as session:
        notices = list(
            await session.scalars(
                select(TelegramOutbox)
                .where(TelegramOutbox.operation == "send_progress_notice")
                .order_by(TelegramOutbox.created_at)
            )
        )
        assert len(notices) == 1
        payloads = {row.business_key: row.payload for row in notices}
        assert "加成 2 次" in payloads["notice:notice-award"]["ticket_status"]
        assert all("暂存" in row.payload["ticket_status"] for row in notices)


async def test_restart_and_cutoff_notices_are_idempotent(session_factory):
    campaign_id, participant_id = await seed(session_factory, days=7, messages=1)
    service = ActivityService(partial(UnitOfWork, session_factory))
    await messages(service, day=0, count=1)
    await messages(service, day=2, count=1)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        await notify_unqualified_at_cutoff(session, campaign)
        await session.flush()
        await notify_unqualified_at_cutoff(session, campaign)
    async with session_factory() as session:
        notices = list(await session.scalars(select(TelegramOutbox)))
        restart = [row for row in notices if row.business_key.startswith("base_restart:")]
        cutoff = [row for row in notices if row.business_key.startswith("base_cutoff:")]
        assert len(restart) == len(cutoff) == 1
        assert "不能参与开奖" in cutoff[0].payload["text"]
        assert not (await session.get(Participant, participant_id)).base_qualified
