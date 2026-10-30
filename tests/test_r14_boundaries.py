from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tests.test_r14_queries import message
from tests.test_r14_start_and_signup import _original_card
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.retention_policy import is_progress_notice
from tg_lottery.application.start_notices import cancel_closed_start, queue_start_notices
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import Participant, TelegramOutbox
from tg_lottery.telegram.lottery_queries import LotteryQueries
from tg_lottery.telegram.member_base_activity import base_registration_reply
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


@pytest.mark.parametrize("chat_id", [-100123, 901])
async def test_personal_progress_reads_real_intent_without_registering(session_factory, chat_id):
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        participant.registration_intent_at = utcnow()
        campaign.rules = dict(campaign.rules) | {"bonus_types": []}
        campaign_ref = campaign.public_ref
    flow = LotteryQueries(QueryService(session_factory), bot_username="testbot")
    await flow.message(message("/抽奖进度", user_id=201, chat_id=chat_id), "/抽奖进度")
    async with session_factory() as session:
        row = await session.scalar(select(TelegramOutbox))
        text = row.payload["text"]
        assert all(word in text for word in ("门槛进度", "加成进度", "邀请进度", "报名待完成"))
        assert campaign_ref in text
        assert row.payload.get("delete_after_seconds") == (20 if chat_id < 0 else None)
        assert ("20秒后删除" in text) == (chat_id < 0)
        participant = await session.scalar(select(Participant))
        assert participant.counting_started_at is None
        assert participant.ticket_total_cache == 0


@pytest.mark.parametrize("text,counts", [("抽奖进度", True), ("/抽奖进度", False)])
async def test_query_does_not_change_original_plain_text_vs_slash_counting(text, counts):
    handler = object.__new__(LotteryUpdateHandler)
    handler._lottery_queries = SimpleNamespace(message=AsyncMock(return_value=True))
    handler._stats = SimpleNamespace(record=AsyncMock())
    handler._activity = SimpleNamespace(record_message=AsyncMock())
    await handler._message(message(text))
    assert handler._stats.record.await_count == int(counts)
    assert handler._activity.record_message.await_count == int(counts)
    if counts:
        assert handler._activity.record_message.await_args.args[0].text == text


@pytest.mark.parametrize(
    "state,expected",
    [(CampaignState.SCHEDULED, "尚未开始"), (CampaignState.PAUSED, "活动暂停")],
)
async def test_private_registration_panel_preserves_progress_without_claiming_early_counts(
    state, expected
):
    campaign = SimpleNamespace(
        public_ref="ref", state=state, cutoff_at=utcnow() + timedelta(hours=1), rules={}
    )
    queries = SimpleNamespace(
        participant_campaign_detail=AsyncMock(
            return_value=SimpleNamespace(base_qualified=False, base_progress="A群 3/5条", total=8)
        )
    )
    panel = SimpleNamespace(
        chat=SimpleNamespace(type="private"), edit_text=AsyncMock(), answer=AsyncMock()
    )
    callback = SimpleNamespace(answer=AsyncMock())
    await base_registration_reply(panel, campaign, user_id=201, callback=callback, queries=queries)
    text = panel.edit_text.await_args.args[0]
    assert expected in text and "A群 3/5条" in text and "有效抽奖次数\uff1a0" in text
    panel.answer.assert_not_called()


@pytest.mark.parametrize("closed", [True, False])
async def test_start_notice_rechecks_state_and_original_publication_authority(
    session_factory, closed
):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        source = await _original_card(session, campaign)
        await queue_start_notices(session, campaign)
        await session.flush()
        notice = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_start_card")
        )
        notice.state = OutboxState.CLAIMED
        notice.lease_owner = "preflight-worker"
        notice.lease_until = utcnow() + timedelta(minutes=2)
        if closed:
            campaign.state = CampaignState.CANCELLED
        else:
            source.recipient_chat_id = -999
    assert await cancel_closed_start(session_factory, notice)
    async with session_factory() as session:
        assert (await session.get(TelegramOutbox, notice.id)).state == OutboxState.CANCELLED


def test_blocked_speech_feedback_is_progress_but_unregistered_nudge_is_not():
    def row(key):
        return SimpleNamespace(operation="send_temporary_reminder", business_key=key)

    assert is_progress_notice(row("blocked_speech:campaign:user"))
    assert not is_progress_notice(row("unregistered:campaign:user"))


async def test_malformed_query_callback_is_alert_only():
    flow = LotteryQueries(SimpleNamespace(), bot_username="testbot")
    query = SimpleNamespace(data="lq:invalid:bad:0", answer=AsyncMock())
    assert await flow.callback(query)
    assert query.answer.await_args.kwargs["show_alert"] is True


async def test_featured_single_card_gets_one_helper_but_digest_and_edits_do_not(session_factory):
    from tg_lottery.application.channel_shortcuts import queue_channel_shortcut

    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        source = await _original_card(session, campaign)
        source.operation = "send_featured_campaign"
        source.recipient_chat_id = -100999
        source.payload = {
            "bot_username": "testbot",
            "items": [{"campaign_ref": campaign.public_ref}],
        }
        await queue_channel_shortcut(session, source)
        await session.flush()
        await queue_channel_shortcut(session, source)
        source.operation = "send_featured_digest"
        source.recipient_chat_id = -100998
        await queue_channel_shortcut(session, source)
        source.operation = "edit_featured_campaign"
        await queue_channel_shortcut(session, source)
        rows = list(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.operation == "send_channel_shortcut")
            )
        )
        assert len(rows) == 1 and rows[0].recipient_chat_id == -100999


async def test_getting_invite_link_cannot_complete_pending_registration():
    from tg_lottery.domain.errors import UserFacingError
    from tg_lottery.telegram.platform_inviter import verify_platform_inviter

    flow = SimpleNamespace(
        _queries=SimpleNamespace(
            participant_campaign_detail=AsyncMock(
                return_value=SimpleNamespace(registration_completed=False)
            )
        ),
        _participants=SimpleNamespace(begin_registration=AsyncMock()),
    )
    with pytest.raises(UserFacingError, match="不会自动报名"):
        await verify_platform_inviter(
            flow,
            SimpleNamespace(rules={"platform_policy": {"enabled": True}}, public_ref="ref"),
            user=SimpleNamespace(id=201),
        )
    flow._participants.begin_registration.assert_not_called()


async def test_summary_preserves_existing_daily_cycles_and_does_not_write_ledger(
    session_factory, monkeypatch
):
    from sqlalchemy import func

    from tests.v30_helpers import START, rules_for, seed_v3, speech
    from tg_lottery.infrastructure.db import TicketLedger
    from tg_lottery.telegram.lottery_query_views import current_task_summary

    cid, _ = await seed_v3(session_factory, rules_for("message", ("activity",)))
    await speech(session_factory, count=3)
    monkeypatch.setattr("tg_lottery.application.group_bonus_progress.utcnow", lambda: START)
    monkeypatch.setattr("tg_lottery.telegram.lottery_query_views.utcnow", lambda: START)
    queries = QueryService(session_factory)
    campaign = await queries.campaign(cid)
    async with session_factory() as session:
        before = await session.scalar(select(func.count()).select_from(TicketLedger))
    text = await current_task_summary(queries, campaign, user_id=201)
    assert "已完成 1 轮" in text and "1/2 条" in text and "还差 1 条" in text
    assert "今日 +1 次" in text and "未配置邀请" in text
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(TicketLedger)) == before


@pytest.mark.parametrize("chat_id", [-100123, 901])
async def test_progress_callback_lifetime_matches_chat_retention(session_factory, chat_id):
    from tg_lottery.domain.enums import DeliveryCertainty
    from tg_lottery.infrastructure.db import DeliveryAttempt

    flow = LotteryQueries(QueryService(session_factory), bot_username="testbot")
    await flow.message(message("/抽奖进度", chat_id=chat_id), "/抽奖进度")
    flow.view = AsyncMock(return_value=("📊 最新进度", []))
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(TelegramOutbox))
        row.state, row.remote_message_id = OutboxState.CONFIRMED, 55
        session.add(
            DeliveryAttempt(
                outbox_id=row.id,
                attempt_number=1,
                telegram_method=row.operation,
                certainty=DeliveryCertainty.CONFIRMED,
                finished_at=utcnow() - timedelta(seconds=21),
                remote_result={},
            )
        )
        key = row.id.hex
    panel = SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=55, edit_text=AsyncMock())
    query = SimpleNamespace(
        data=f"lq:{key}:0:0", message=panel, from_user=SimpleNamespace(id=901), answer=AsyncMock()
    )
    assert await flow.callback(query)
    if chat_id < 0:
        assert "过期" in query.answer.await_args.args[0]
        panel.edit_text.assert_not_called()
        flow.view.assert_not_awaited()
    else:
        panel.edit_text.assert_awaited_once()
        flow.view.assert_awaited_once()
        query.answer.assert_not_awaited()
    async with session_factory() as session:
        assert len(list(await session.scalars(select(TelegramOutbox)))) == 1


async def test_channel_overlay_api_failure_never_exposes_personal_progress_publicly(
    session_factory, monkeypatch
):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendMessage

    from tg_lottery.application.channel_shortcuts import queue_channel_shortcut
    from tg_lottery.telegram.channel_shortcuts import handle_shortcut
    from tg_lottery.telegram.ui_style import row_keyboard

    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        source = await _original_card(session, campaign, channel=True)
        await queue_channel_shortcut(session, source)
        await session.flush()
        helper = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_channel_shortcut")
        )
        helper.state, helper.remote_message_id = OutboxState.CONFIRMED, 66
    bot = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=-100123, text="private"), message="unsupported"
        )
    )
    flow = SimpleNamespace(
        _queries=QueryService(session_factory),
        _bot=bot,
        _profiles=object(),
        _cipher=object(),
        _relationships=None,
        _bot_username="testbot",
    )
    check = AsyncMock(return_value=("本人进度", row_keyboard([])))
    monkeypatch.setattr("tg_lottery.telegram.channel_shortcuts.condition_check_payload", check)
    query = SimpleNamespace(
        data=f"cp:{helper.id.hex}:0",
        id="callback",
        message=message("source", number=66),
        from_user=SimpleNamespace(id=901),
        answer=AsyncMock(),
    )
    assert await handle_shortcut(flow, query)
    assert "进入机器人" in query.answer.await_args.args[0]
    assert check.await_args.kwargs["page"] == 0
    bot.send_message.assert_not_called()
    bot.edit_message_text.assert_not_called()
