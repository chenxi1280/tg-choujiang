from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from tests.test_v33_unlimited_runtime import current_rules
from tests.v30_helpers import seed_v3
from tg_lottery.application.queries import QueryService
from tg_lottery.application.reputation_queries import ReputationPage, ReputationRow
from tg_lottery.domain.enums import CampaignState, QualificationState, TicketSource
from tg_lottery.infrastructure.db import Participant, TicketLedger
from tg_lottery.telegram.group_reputation import GroupReputation
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


def campaign(reference="a" * 22, *, state=CampaignState.RUNNING):
    return SimpleNamespace(
        id="id",
        public_ref=reference,
        name="活动",
        rules=current_rules().model_dump(),
        presentation={},
        state=state,
        cutoff_at=datetime.now(UTC) + timedelta(days=1),
    )


def message(text="人品"):
    return SimpleNamespace(
        text=text,
        migrate_to_chat_id=None,
        chat=SimpleNamespace(id=-100123, type="supergroup"),
        from_user=SimpleNamespace(id=201, is_bot=False),
        answer=AsyncMock(),
        edit_text=AsyncMock(),
    )


@pytest.mark.parametrize("text", ["人品", "人品排行", "人品规则"])
async def test_group_query_never_enters_message_counting(text):
    queries = SimpleNamespace(active_campaigns_by_chat=AsyncMock(return_value=[]))
    stats, activity = (
        SimpleNamespace(record=AsyncMock()),
        SimpleNamespace(record_message=AsyncMock()),
    )
    handler = LotteryUpdateHandler(
        bot=None,
        default_timezone="Asia/Shanghai",
        admin_flow=None,
        member_flow=None,
        personal_flow=None,
        tokens=None,
        groups=None,
        activity=activity,
        invitations=None,
        queries=queries,
        stats=stats,
        bot_username="lottery_bot",
        navigation=None,
    )
    incoming = message(text)
    await handler._message(incoming)
    stats.record.assert_not_awaited()
    activity.record_message.assert_not_awaited()
    assert "没有" in incoming.answer.call_args.args[0]


async def test_multiple_campaigns_require_selection_and_unpublished_draft_is_hidden():
    values = [campaign(), campaign("b" * 22), campaign("c" * 22, state=CampaignState.DRAFT)]
    queries = SimpleNamespace(active_campaigns_by_chat=AsyncMock(return_value=values))
    shortcut = GroupReputation(queries=queries, bot_username="lottery_bot")
    incoming = message()
    await shortcut.message(incoming, "人品")
    text, markup = (
        incoming.answer.call_args.args[0],
        incoming.answer.call_args.kwargs["reply_markup"],
    )
    assert "选择" in text and len(markup.inline_keyboard) == 2
    assert all(len(row[0].callback_data.encode()) <= 64 for row in markup.inline_keyboard)


async def test_unregistered_self_query_is_read_only_and_has_same_campaign_entry():
    queries = SimpleNamespace(
        active_campaigns_by_chat=AsyncMock(return_value=[campaign()]),
        participant_campaign_detail=AsyncMock(return_value=None),
    )
    incoming = message()
    await GroupReputation(queries=queries, bot_username="lottery_bot").message(incoming, "人品")
    assert "不会自动报名" in incoming.answer.call_args.args[0]
    assert (
        "join_" + "a" * 22
        in incoming.answer.call_args.kwargs["reply_markup"].inline_keyboard[-1][0].url
    )


async def test_foreign_user_cannot_read_owner_query_and_rank_explains_staged_rewards():
    queries = SimpleNamespace(
        active_campaigns_by_chat=AsyncMock(return_value=[campaign()]),
        reputation_ranking=AsyncMock(
            return_value=ReputationPage((ReputationRow(201, "甲<&", 20, 1),), 0, 2)
        ),
    )
    shortcut = GroupReputation(queries=queries, bot_username="lottery_bot")
    incoming = message()
    await shortcut.message(incoming, "人品排行")
    text = incoming.answer.call_args.args[0]
    assert "含暂存" in text and "甲&lt;&amp;" in text
    query = SimpleNamespace(
        message=incoming, from_user=SimpleNamespace(id=202), data="rep:rank:" + "a" * 22 + ":201:1"
    )
    await shortcut.callback(query)
    queries.reputation_ranking.assert_awaited_once()
    incoming.edit_text.assert_not_awaited()


async def test_reputation_rank_sums_only_activity_ledger_with_ties_and_paging(session_factory):
    campaign_id, first = await seed_v3(session_factory, current_rules())
    async with session_factory() as session, session.begin():
        for index in range(12):
            participant = (
                await session.get(Participant, first)
                if index == 0
                else Participant(
                    campaign_id=campaign_id,
                    user_id=201 + index,
                    display_name=f"群友{index}",
                    admission_qualified=False,
                    qualification_state=QualificationState.ELIGIBLE,
                    registered_revision=1,
                )
            )
            session.add(participant)
            await session.flush()
            session.add(
                TicketLedger(
                    campaign_id=campaign_id,
                    participant_id=participant.id,
                    source_type=TicketSource.POPULARITY,
                    delta=20 if index < 2 else 12 - index,
                    source_ref=f"rank-{index}",
                    business_key=f"rank-{index}",
                    occurred_at=datetime.now(UTC),
                )
            )
        session.add(
            TicketLedger(
                campaign_id=campaign_id,
                participant_id=first,
                source_type=TicketSource.INVITATION,
                delta=9999,
                source_ref="invitation",
                business_key="invitation",
                occurred_at=datetime.now(UTC),
            )
        )
    queries = QueryService(session_factory)
    page = await queries.reputation_ranking(campaign_id)
    assert page.pages == 2 and len(page.rows) == 10
    assert [row.rank for row in page.rows[:3]] == [1, 1, 3]
    assert [row.user_id for row in page.rows[:2]] == [201, 202]
    assert page.rows[0].tickets == 20
    second = await queries.reputation_ranking(campaign_id, page=100)
    assert second.page == 1 and len(second.rows) == 2
    async with session_factory() as session:
        assert await session.scalar(select(func.count(TicketLedger.id))) == 14
