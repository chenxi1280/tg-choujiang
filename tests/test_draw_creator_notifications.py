from datetime import UTC, datetime
from functools import partial

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from sqlalchemy import select

from tests.test_runtime_integrity import _cipher
from tests.test_telegram_gateway_messages import FakeCardBot, _sent_message
from tests.test_winner_designations import _seed_case, _settle_and_draw
from tg_lottery.application.drawing import DrawingService
from tg_lottery.domain.enums import DeliveryCertainty, OutboxState, QualificationState
from tg_lottery.infrastructure.db import (
    Campaign,
    DeliveryAttempt,
    Participant,
    TelegramOutbox,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.presentation.draw_messages import draw_result_pages, draw_result_text
from tg_lottery.presentation.mentions import MESSAGE_UTF16_LIMIT, html_utf16_length
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.outbox import OutboxWorker


async def _draw(session_factory, *, empty=False, creator_wins=False, creator_id=101, repeat=False):
    now = datetime.now(UTC)
    campaign_id, _, _ = await _seed_case(session_factory, now, winner_count=2)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.created_by = creator_id
        campaign.presentation = {
            "message_tips": False,
            "issuer_user_id": 999,
            "issuer_name": "另一个发奖人",
            "issuer_username_at_publish": "issuer",
        }
        participants = tuple(await session.scalars(select(Participant)))
        if empty:
            for participant in participants:
                participant.qualification_state = QualificationState.INELIGIBLE
        if creator_wins:
            participants[0].user_id = campaign.created_by
        if repeat:
            campaign.rules = campaign.rules | {"unique_winner": False}
            participants[1].qualification_state = QualificationState.INELIGIBLE
    await _settle_and_draw(session_factory, campaign_id, now)
    await DrawingService(partial(UnitOfWork, session_factory)).run(campaign_id)
    async with session_factory() as session:
        return tuple(await session.scalars(select(TelegramOutbox)))


@pytest.mark.parametrize("creator_wins", [False, True])
async def test_draw_queues_creator_and_winner_notices_independently_once(
    session_factory, creator_wins
):
    rows = await _draw(session_factory, creator_wins=creator_wins)
    creator = [row for row in rows if row.operation == "send_creator_draw_notice"]
    winners = [row for row in rows if row.operation == "send_winner_notice"]
    groups = [row for row in rows if row.operation == "send_draw_result"]
    assert len(creator) == 1 and creator[0].recipient_chat_id == 101
    assert creator[0].aggregate_type == "draw_run"
    assert creator[0].payload["winner_count"] == creator[0].payload["awarded_slots"] == 2
    assert len(winners) == 2
    assert [row.payload["issuer_user_id"] for row in winners] == [999, 999]
    assert [row.payload["extended_tips"] for row in winners] == [False, False]
    assert any(row.recipient_chat_id == 101 for row in winners) is creator_wins
    assert groups[0].payload["creator"]["user_id"] == 101
    assert "id=999" not in draw_result_text(groups[0].payload)
    assert 'href="tg://user?id=101"' in draw_result_text(groups[0].payload)
    assert len({row.business_key for row in rows}) == len(rows)


async def test_empty_draw_still_notifies_creator(session_factory):
    rows = await _draw(session_factory, empty=True)
    assert not any(row.operation == "send_winner_notice" for row in rows)
    notice = next(row for row in rows if row.operation == "send_creator_draw_notice")
    assert notice.recipient_chat_id == 101
    assert notice.payload["winner_count"] == notice.payload["awarded_slots"] == 0
    assert notice.payload["undistributed_count"] == 2
    assert all(
        "创建者" in draw_result_text(row.payload)
        for row in rows
        if row.operation == "send_draw_result"
    )


async def test_creator_is_distinct_from_manual_draw_operator_and_repeat_winners_count_once(
    session_factory,
):
    rows = await _draw(session_factory, creator_id=777, repeat=True)
    creator = next(row for row in rows if row.operation == "send_creator_draw_notice")
    assert creator.recipient_chat_id == 777
    assert creator.payload["winner_count"] == 1 and creator.payload["awarded_slots"] == 2
    winners = [row for row in rows if row.operation == "send_winner_notice"]
    assert len(winners) == 2
    assert winners[0].recipient_chat_id == winners[1].recipient_chat_id == 201
    group = next(row for row in rows if row.operation == "send_draw_result")
    assert 'href="tg://user?id=777"' in draw_result_text(group.payload)


def test_creator_mention_uses_escaped_name_when_creator_is_issuer():
    from tg_lottery.application.draw_notifications import _creator_payload

    campaign = Campaign(
        created_by=101,
        presentation={
            "issuer_user_id": 101,
            "issuer_name": "<创建者>&",
            "issuer_username_at_publish": None,
        },
    )
    text = draw_result_text({"campaign_name": "场", "creator": _creator_payload(campaign)})
    assert 'href="tg://user?id=101">@&lt;创建者&gt;&amp;</a>' in text


def test_long_group_results_budget_creator_mention_and_only_mention_on_first_page():
    payload = {
        "campaign_name": "大场",
        "creator": {"user_id": 101, "display_name": "😀<&>" * 128},
        "winners": [
            {
                "user_id": index,
                "display_name": "😀<&>" * 30,
                "prize_position": 1,
                "prize_name": "长奖品😀" * 20,
                "prize_slot_no": index,
            }
            for index in range(201, 260)
        ],
    }
    pages = draw_result_pages(payload)
    assert len(pages) > 1
    assert [item for page in pages for item in page["winners"]] == payload["winners"]
    for index, page in enumerate(pages):
        text = draw_result_text(page)
        assert html_utf16_length(text) <= MESSAGE_UTF16_LIMIT
        assert ('href="tg://user?id=101"' in text) is (index == 0)


class _SelectiveBot(FakeCardBot):
    def __init__(self, failure):
        super().__init__()
        self.failure = failure

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id == 101 and self.failure == "forbidden":
            raise TelegramForbiddenError(
                method=SendMessage(chat_id=chat_id, text=text),
                message="bot was blocked by the user",
            )
        if chat_id == 101 and self.failure == "unknown":
            raise TimeoutError("uncertain transport")
        return await super().send_message(chat_id, text, **kwargs)

    async def edit_message_text(self, text, **kwargs):
        return _sent_message()


@pytest.mark.parametrize(
    "failure,state,certainty",
    [
        (None, OutboxState.CONFIRMED, DeliveryCertainty.CONFIRMED),
        ("forbidden", OutboxState.PRE_ACCEPT_FAILED, DeliveryCertainty.EXPLICIT_REJECTION),
        ("unknown", OutboxState.UNKNOWN_HOLD, DeliveryCertainty.UNKNOWN),
    ],
)
async def test_real_outbox_worker_keeps_creator_delivery_separate_from_winners(
    session_factory, *, failure, state, certainty
):
    await _draw(session_factory)
    bot = _SelectiveBot(failure)
    worker = OutboxWorker(
        owner_id="draw-notice-test",
        session_factory=session_factory,
        gateway=TelegramGateway(bot, _cipher(), bot_username="test_bot"),
        cipher=_cipher(),
    )
    while await worker.process_once():
        pass
    async with session_factory() as session:
        rows = tuple(await session.scalars(select(TelegramOutbox)))
        creator = next(row for row in rows if row.operation == "send_creator_draw_notice")
        attempts = tuple(
            await session.scalars(
                select(DeliveryAttempt).where(DeliveryAttempt.outbox_id == creator.id)
            )
        )
    assert creator.state == state
    assert len(attempts) == 1 and attempts[0].certainty == certainty
    assert all(
        row.state == OutboxState.CONFIRMED for row in rows if row.operation == "send_winner_notice"
    )
    assert await worker.process_once() is False
    if failure is None:
        sent = [item for item in bot.sent if item[0] == "send_message" and item[1][0] == 101]
        assert len(sent) == 1
        assert "已开奖" in sent[0][1][1] and "?start=history_" in sent[0][1][1]
        assert sent[0][2]["disable_notification"] is False
