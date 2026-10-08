from datetime import UTC, datetime, timedelta
from functools import partial
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from tests.helpers import seed_campaign
from tests.reminder_neighbor_helpers import CHAT_ID, flow_for, handler_for
from tests.test_outbox_projection import _cipher
from tests.test_v32_member_navigation import _query
from tests.v30_helpers import FIRST, rules_for, seed_v3, speech
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState, QualificationState, TicketSource
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    DailyProgress,
    InviteLink,
    Participant,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.member_callbacks import bonus_callback, show_bonus_message


@pytest.mark.parametrize(
    "state", [QualificationState.INELIGIBLE, QualificationState.UNKNOWN, QualificationState.PENDING]
)
async def test_registered_unqualified_user_reads_bonus_without_re_registering_or_new_invite(
    session_factory, monkeypatch, state
):
    campaign_id, participant_id = await seed_v3(
        session_factory, rules_for("message", ("activity", "invite"))
    )
    await speech(session_factory, count=2)
    async with session_factory() as session, session.begin():
        participant = await session.get(Participant, participant_id)
        participant.qualification_state = state
    queries = QueryService(session_factory)
    campaign = await queries.campaign(campaign_id)
    participants = ParticipantService(partial(UnitOfWork, session_factory))
    flow = flow_for(queries, participants=participants)
    before = await queries.participant_campaign_detail(201, campaign.public_ref)
    popup, answer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", popup)
    monkeypatch.setattr(Message, "answer", answer)
    query = _query(CHAT_ID, kind="supergroup")
    query = query.model_copy(update={"from_user": query.from_user.model_copy(update={"id": 201})})
    await bonus_callback(flow, query, campaign.public_ref)
    assert flow._bot.send_message.await_args.args[0] == 201
    text = flow._bot.send_message.await_args.args[1]
    assert "历史奖励保留" in text
    markup = flow._bot.send_message.await_args.kwargs["reply_markup"]
    assert not any(
        (b.callback_data or "").startswith("member:invite_group:")
        for row in markup.inline_keyboard
        for b in row
    )
    await show_bonus_message(flow, _query(201).message, campaign.public_ref, user_id=201)
    assert "历史奖励保留" in answer.await_args.args[0]
    with pytest.raises(UserFacingError):
        await participants.request_group_invite_link(
            campaign_id, 201, FIRST, bot_username="test_bot"
        )
    after = await queries.participant_campaign_detail(201, campaign.public_ref)
    assert (before.total, before.activity, before.qualification) == (
        after.total,
        after.activity,
        after.qualification,
    )
    await _assert_no_registration_or_invite_write(session_factory)


async def _assert_no_registration_or_invite_write(factory):
    async with factory() as session:
        assert await session.scalar(select(func.count(Participant.id))) == 1
        assert (
            await session.scalar(
                select(func.count(TelegramOutbox.id)).where(
                    TelegramOutbox.operation == "create_invite_link"
                )
            )
            == 0
        )


async def _seed_legacy(factory, state):
    async with factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(
            session, state=state, rules=CampaignRules(bonus_types=("activity",))
        )
        session.add(
            DailyProgress(
                campaign_id=campaign.id,
                participant_id=participant.id,
                local_date=datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai")).date(),
                valid_message_counts={str(group.chat_id): 4},
                tickets_awarded=1,
            )
        )
        session.add(
            TicketLedger(
                campaign_id=campaign.id,
                participant_id=participant.id,
                delta=2,
                source_type=TicketSource.POPULARITY,
                source_ref="historical",
                business_key="old-award",
                occurred_at=datetime.now(UTC),
            )
        )
        return campaign.id


@pytest.mark.parametrize("state", [CampaignState.RUNNING, CampaignState.DRAWN])
async def test_legacy_activity_only_card_deep_link_and_navigation_read_original_facts(
    session_factory, monkeypatch, state
):
    campaign_id = await _seed_legacy(session_factory, state)
    queries = QueryService(session_factory)
    campaign = await queries.campaign(campaign_id)
    participants = ParticipantService(partial(UnitOfWork, session_factory))
    flow = flow_for(queries, participants=participants)
    popup, answer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", popup)
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "edit_text", answer)
    query = _query(CHAT_ID, kind="supergroup")
    query = query.model_copy(update={"from_user": query.from_user.model_copy(update={"id": 201})})
    await bonus_callback(flow, query, campaign.public_ref)
    text = flow._bot.send_message.await_args.args[1]
    assert "累计活跃加成\uff08人品\uff09: 2" in text
    assert "4/10 条" in text if state is CampaignState.RUNNING else "不再计算新增" in text
    private = query.model_copy(update={"message": _query(201).message})
    await show_bonus_message(flow, private.message, campaign.public_ref, user_id=201)
    assert "累计活跃加成\uff08人品\uff09: 2" in answer.await_args.args[0]
    group = query.model_copy(update={"data": f"m:bo:{campaign.public_ref}"})
    await handler_for(flow)._callback(group)
    assert "累计活跃加成\uff08人品\uff09: 2" in answer.await_args.args[0]
    async with session_factory() as session:
        assert await session.scalar(select(func.count(InviteLink.id))) == 0
        assert await session.scalar(select(func.count(TicketLedger.id))) == 1
        assert await session.scalar(select(func.count(Participant.id))) == 1


async def test_old_group_callback_replay_creates_one_temporary_outbox(session_factory, monkeypatch):
    campaign_id, _ = await seed_v3(session_factory, rules_for("message", ("activity",)))
    queries = QueryService(session_factory)
    campaign = await queries.campaign(campaign_id)
    flow = flow_for(queries, participants=ParticipantService(partial(UnitOfWork, session_factory)))
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    query = _query(CHAT_ID, kind="supergroup").model_copy(
        update={"data": f"m:bo:{campaign.public_ref}"}
    )
    handler = handler_for(flow)
    await handler._callback(query)
    await handler._callback(query)
    async with session_factory() as session:
        rows = tuple(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.operation == "send_temporary_reminder")
            )
        )
        assert len(rows) == 1
        assert rows[0].recipient_chat_id == CHAT_ID
        assert rows[0].payload["delete_after_seconds"] == 15
        assert rows[0].payload["campaign_ref"] == campaign.public_ref


@pytest.mark.parametrize("pending_link", [False, True])
async def test_legacy_invite_record_stays_readable_before_personal_link_is_confirmed(
    session_factory, monkeypatch, pending_link
):
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session, rules=CampaignRules(bonus_types=("invite",))
        )
        if pending_link:
            session.add(
                InviteLink(
                    campaign_id=campaign.id,
                    participant_id=participant.id,
                    telegram_name="pending",
                    link_hash="d" * 64,
                )
            )
        campaign_id = campaign.id
    queries = QueryService(session_factory)
    campaign = await queries.campaign(campaign_id)
    flow = flow_for(queries, participants=ParticipantService(partial(UnitOfWork, session_factory)))
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    query = _query(CHAT_ID, kind="supergroup")
    query = query.model_copy(update={"from_user": query.from_user.model_copy(update={"id": 201})})
    await bonus_callback(flow, query, campaign.public_ref)
    assert flow._bot.send_message.await_args.args[0] == 201
    assert "累计邀请加成: 0" in flow._bot.send_message.await_args.args[1]
    async with session_factory() as session:
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 0
        assert await session.scalar(select(func.count(InviteLink.id))) == int(pending_link)


async def _seed_confirmed_legacy_invite(factory, cipher, status):
    async with factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session,
            state=CampaignState.DRAWN if status == "drawn" else CampaignState.RUNNING,
            rules=CampaignRules(bonus_types=("invite",)),
        )
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            telegram_name="confirmed",
            link_hash="e" * 64,
            revoked_at=datetime.now(UTC) if status == "revoked" else None,
            expire_at=datetime.now(UTC) - timedelta(minutes=1) if status == "expired" else None,
        )
        session.add(link)
        await session.flush()
        link.encrypted_url = cipher.encrypt(
            "https://t.me/+test_legacy_personal", context=f"invite-link:{link.id}"
        )
        return campaign.id


@pytest.mark.parametrize("kind", ["private", "supergroup"])
@pytest.mark.parametrize("status", ["active", "revoked", "expired", "drawn"])
async def test_confirmed_legacy_invite_opens_through_actual_bonus_navigation(
    session_factory, monkeypatch, *, kind, status
):
    cipher = _cipher()
    campaign_id = await _seed_confirmed_legacy_invite(session_factory, cipher, status)
    queries = QueryService(session_factory)
    campaign = await queries.campaign(campaign_id)
    flow = flow_for(queries)
    flow._cipher = cipher
    rendered = []

    async def record(message, text, **options):
        rendered.append((message, text, options["reply_markup"]))

    monkeypatch.setattr(Message, "answer", record)
    monkeypatch.setattr(Message, "edit_text", record)
    popup = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", popup)
    query = _query(201 if kind == "private" else CHAT_ID, kind=kind)
    query = query.model_copy(
        update={
            "from_user": query.from_user.model_copy(update={"id": 201}),
            "data": f"m:bo:{campaign.public_ref}",
        }
    )
    await handler_for(flow)._callback(query)
    assert len(rendered) == 1
    message, text, keyboard = rendered[0]
    assert message.chat.id == 201 and message.chat.type == "private"
    expected = {
        "active": "专属邀请链接",
        "revoked": "个人邀请链接已撤销",
        "expired": "个人邀请链接已过期",
        "drawn": "不再计算本期或以后任何抽奖加成",
    }
    assert expected[status] in text
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert any(button.copy_text for button in buttons) is (status == "active")
    assert any(button.url == "https://t.me/+test_legacy_personal" for button in buttons) is (
        status == "active"
    )
    popup.assert_awaited()
    async with session_factory() as session:
        assert await session.scalar(select(func.count(InviteLink.id))) == 1
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 0
        assert await session.scalar(select(func.count(TicketLedger.id))) == 0
