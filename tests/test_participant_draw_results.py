# ruff: noqa: RUF001

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application import base_activity_views
from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.participant_records import ParticipantCampaignDetail
from tg_lottery.application.participant_results import ParticipantAward, ParticipantDrawResult
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.application.queries import QueryService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.domain.enums import CampaignState, OutboxState, QualificationState, TicketSource
from tg_lottery.domain.rules import BaseActivityRule, CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    DrawRun,
    DrawSnapshot,
    Participant,
    PollingOffset,
    Prize,
    QualificationCheck,
    TelegramOutbox,
    TicketLedger,
    Winner,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.presentation.mentions import MESSAGE_UTF16_LIMIT
from tg_lottery.telegram.member_record_text import participant_record_pages
from tg_lottery.telegram.member_records import show_ticket_record

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"


async def test_winner_reads_snapshot_and_awards_despite_failed_notifications(session_factory):
    campaign_id, public_ref = await _completed_draw(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.state = CampaignState.ARCHIVED
        participant = await session.scalar(select(Participant).where(Participant.user_id == 201))
        session.add(_ticket(campaign_id, participant, amount=9, source="after-draw"))
        for notice in await session.scalars(select(TelegramOutbox)):
            notice.state = OutboxState.PRE_ACCEPT_FAILED
    detail = await QueryService(session_factory).participant_campaign_detail(201, public_ref)

    assert detail.total == 12
    assert detail.draw_result.frozen_tickets == 3
    assert [(award.prize_name, award.prize_slot_no) for award in detail.draw_result.awards] == [
        ("一等奖", 1),
        ("一等奖", 2),
    ]
    assert detail.draw_result.issuer_user_id == 301
    text = "\n".join(participant_record_pages(detail))
    assert "已中奖" in text and "最终开奖次数：3" in text
    assert "联系管理员" in text and "@prize_issuer" in text
    assert "实际开奖时间：" in text


async def test_final_snapshot_exclusion_is_distinct_from_not_winning(session_factory):
    _, public_ref = await _completed_draw(session_factory)
    queries = QueryService(session_factory)
    excluded = await queries.participant_campaign_detail(202, public_ref)

    assert excluded.draw_result.frozen_tickets is None
    assert excluded.draw_result.awards == ()
    text = "\n".join(participant_record_pages(excluded))
    assert "未进入最终开奖名单" in text
    assert "未中奖" not in text
    assert await queries.participant_campaign_detail(999, public_ref) is None


async def test_non_winner_is_scoped_to_clicking_participant_and_campaign(session_factory):
    _, public_ref = await _completed_draw(session_factory, second_eligible=True)
    async with session_factory() as session:
        winner_user_id = await session.scalar(
            select(Participant.user_id).join(Winner, Winner.participant_id == Participant.id)
        )
    losing_user_id = 202 if winner_user_id == 201 else 201
    queries = QueryService(session_factory)
    detail = await queries.participant_campaign_detail(losing_user_id, public_ref)

    assert detail.draw_result.frozen_tickets == 3
    assert detail.draw_result.awards == ()
    assert "开奖结果：未中奖" in "\n".join(participant_record_pages(detail))
    assert await queries.participant_campaign_detail(losing_user_id, "other-campaign") is None


@pytest.mark.parametrize("run_status", ["missing_snapshot", "missing_run", "unfinished_run"])
async def test_missing_completed_result_never_claims_not_winning(session_factory, run_status):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session, state=CampaignState.DRAWN)
        public_ref = campaign.public_ref
        if run_status != "missing_snapshot":
            snapshot = _snapshot(campaign.id)
            session.add(snapshot)
            await session.flush()
            if run_status == "unfinished_run":
                session.add(
                    DrawRun(
                        snapshot_id=snapshot.id,
                        algorithm_version="test",
                        random_input="saved-random-input",
                    )
                )
    detail = await QueryService(session_factory).participant_campaign_detail(201, public_ref)

    assert detail.draw_result is None
    text = "\n".join(participant_record_pages(detail))
    assert "未找到已完成的开奖记录" in text
    assert "未中奖" not in text


@pytest.mark.parametrize("state", [CampaignState.RUNNING, CampaignState.CANCELLED])
def test_unfinished_and_cancelled_campaigns_do_not_claim_a_draw_result(state):
    outcome = CampaignOutcomeFacts("cancelled") if state is CampaignState.CANCELLED else None
    text = "\n".join(participant_record_pages(replace(_detail(), state=state, outcome=outcome)))
    assert "已中奖" not in text and "未中奖" not in text
    assert ("活动已取消，未开奖" in text) == (state == CampaignState.CANCELLED)


async def test_personal_result_pages_preserve_all_awards_and_plain_text():
    award = ParticipantAward(1, "<奖品>&😀", 1, "😀" * 500)
    result = ParticipantDrawResult(NOW, 3, (award,) * 12, "<发奖人>", 301, None)
    detail = replace(_detail(), campaign_name="<活动>&", draw_result=result)
    message = _recording_message()
    queries = SimpleNamespace(participant_campaign_detail=AsyncMock(return_value=detail))

    await show_ticket_record(message, queries, 201, public_ref=PUBLIC_REF)

    texts = [call.args[0] for call in message.answer.call_args_list]
    assert len(texts) > 1
    assert all(len(text.encode("utf-16-le")) // 2 <= MESSAGE_UTF16_LIMIT for text in texts)
    assert "\n".join(texts).count("领奖方式：") == 12
    assert "<活动>&" in texts[0] and "<奖品>&😀" in "\n".join(texts)
    assert all(call.kwargs["parse_mode"] is None for call in message.answer.call_args_list)
    assert all(call.kwargs["reply_markup"] is None for call in message.answer.call_args_list[:-1])
    keyboard = message.answer.call_args_list[-1].kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].url == "tg://user?id=301"
    assert keyboard.inline_keyboard[-1][0].callback_data == "nav:join:ended:0"
    queries.participant_campaign_detail.assert_awaited_once_with(201, PUBLIC_REF)


@pytest.mark.parametrize(
    "state,category",
    [
        (CampaignState.RUNNING, "active"),
        (CampaignState.SETTLING, "pending"),
        (CampaignState.READY_TO_DRAW, "pending"),
        (CampaignState.DRAWING, "pending"),
        (CampaignState.DRAWN, "ended"),
        (CampaignState.ARCHIVED, "ended"),
        (CampaignState.CANCELLED, "ended"),
    ],
)
async def test_detail_returns_to_its_activity_category(state, category):
    message = _recording_message()
    queries = SimpleNamespace(
        participant_campaign_detail=AsyncMock(return_value=replace(_detail(), state=state))
    )
    await show_ticket_record(message, queries, 201, public_ref=PUBLIC_REF)

    keyboard = message.answer.call_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[-1][0].callback_data == f"nav:join:{category}:0"


@pytest.mark.parametrize(
    "state",
    [
        CampaignState.SETTLING,
        CampaignState.READY_TO_DRAW,
        CampaignState.DRAWING,
        CampaignState.DRAWN,
        CampaignState.ARCHIVED,
        CampaignState.CANCELLED,
    ],
)
def test_closed_detail_counts_are_history_and_never_promised_as_stashed(state):
    detail = replace(_detail(), state=state, base_qualified=False)
    text = "\n".join(participant_record_pages(detail))

    assert "历史累计次数（账本记录）: 3" in text
    assert "历史累计不代表最终开奖次数" in text
    assert "暂存" not in text


@pytest.mark.parametrize(
    "state,past_cutoff,completed",
    [
        (CampaignState.RUNNING, False, False),
        (CampaignState.RUNNING, True, True),
        (CampaignState.DRAWN, False, True),
        (CampaignState.CANCELLED, False, True),
    ],
)
async def test_closed_base_progress_is_history_without_continuation_promises(
    session_factory, monkeypatch, *, state, past_cutoff, completed
):
    monkeypatch.setattr(base_activity_views, "utcnow", lambda: NOW)
    delta = -1 if past_cutoff else 1
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(
            session,
            state=state,
            cutoff_at=NOW + timedelta(days=delta),
            rules=CampaignRules(base_activity_rules={"-100123": BaseActivityRule()}),
        )
        participant.base_qualified = False
        participant.ticket_total_cache = 88
        text = await base_activity_views.base_progress_text(session, campaign, participant)

    assert ("本期基础发言已停止累计" in text) == completed
    assert ("加成暂存" in text) != completed
    assert ("当前有效抽奖次数" in text) != completed
    assert ("记录日期" in text) == completed
    assert "名称待更新" in text and "-100123" not in text
    if completed:
        assert "历史进度" in text
        assert "缺勤会中断连续进度" not in text
        assert "88" not in text


def _detail() -> ParticipantCampaignDetail:
    return ParticipantCampaignDetail(
        public_ref=PUBLIC_REF,
        campaign_name="测试活动",
        state=CampaignState.DRAWN,
        qualification=QualificationState.ELIGIBLE,
        total=3,
        registration=3,
        activity=0,
        invitation=0,
        bonus_enabled=False,
        invite_enabled=False,
        draw_at=NOW,
        timezone="Asia/Shanghai",
    )


def _recording_message():
    return SimpleNamespace(chat=SimpleNamespace(id=201, type="private"), answer=AsyncMock())


async def _completed_draw(session_factory, *, second_eligible=False):
    async with session_factory() as session, session.begin():
        _, campaign, first = await seed_campaign(
            session,
            state=CampaignState.SETTLING,
            cutoff_at=NOW - timedelta(minutes=10),
            rules=CampaignRules(unique_winner=second_eligible),
        )
        campaign.presentation = {
            "issuer_user_id": 301,
            "issuer_name": "发奖人",
            "issuer_username_at_publish": "prize_issuer",
        }
        prize = await session.scalar(select(Prize).where(Prize.campaign_id == campaign.id))
        prize.winner_count = 1 if second_eligible else 2
        campaign.prize_plan_hash = prize_plan_hash((prize,))
        second = _second_participant(campaign.id, eligible=second_eligible)
        session.add(second)
        await session.flush()
        session.add(_polling())
        session.add_all(_draw_checks((first, second)))
        session.add(_ticket(campaign.id, first, amount=3, source="first"))
        session.add(_ticket(campaign.id, second, amount=3, source="second"))
        campaign_id, public_ref = campaign.id, campaign.public_ref
    uow = partial(UnitOfWork, session_factory)
    await SettlementService(uow).settle(campaign_id, NOW)
    await DrawingService(uow).run(campaign_id)
    return campaign_id, public_ref


def _second_participant(campaign_id, *, eligible):
    return Participant(
        campaign_id=campaign_id,
        user_id=202,
        display_name="成员二",
        private_reachable=True,
        qualification_state=(
            QualificationState.ELIGIBLE if eligible else QualificationState.INELIGIBLE
        ),
        registered_revision=1,
    )


def _draw_checks(participants):
    return tuple(
        QualificationCheck(
            participant_id=participant.id,
            phase="draw",
            state=participant.qualification_state,
            observed_status="not_required",
            observed_at=NOW,
        )
        for participant in participants
    )


def _ticket(campaign_id, participant, *, amount, source):
    return TicketLedger(
        campaign_id=campaign_id,
        participant_id=participant.id,
        delta=amount,
        source_type=TicketSource.REGISTRATION,
        source_ref=source,
        business_key=f"result-read:{source}:{participant.id}",
        occurred_at=NOW - timedelta(minutes=20),
    )


def _polling():
    return PollingOffset(
        bot_id=123,
        last_durable_update_id=50,
        last_poll_succeeded_at=NOW,
        updated_at=NOW,
    )


def _snapshot(campaign_id):
    return DrawSnapshot(
        campaign_id=campaign_id,
        config_revision=1,
        ledger_watermark=NOW,
        snapshot_hash="a" * 64,
        total_weight=0,
    )
