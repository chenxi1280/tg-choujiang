from datetime import UTC, datetime
from functools import partial

import pytest
from sqlalchemy import select

from tests.test_winner_designations import _draw_check, _seed_case, _settle_and_draw, _ticket
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.application.winners import WinnerService
from tg_lottery.domain.enums import PrizeClaimState, QualificationState
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.infrastructure.db import Campaign, Participant, Prize, TelegramOutbox, Winner
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.presentation.draw_messages import draw_result_text
from tg_lottery.presentation.mentions import MESSAGE_UTF16_LIMIT, html_utf16_length
from tg_lottery.telegram.gateway_requests import build_gateway_request


async def _large_campaign(session_factory, now):
    campaign_id, _, _ = await _seed_case(session_factory, now, winner_count=12)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        prize = await session.scalar(select(Prize).where(Prize.campaign_id == campaign_id))
        prize.name = "🎁" * 100
        campaign.prize_plan_hash = prize_plan_hash((prize,))
        for row in await session.scalars(
            select(Participant).where(Participant.campaign_id == campaign_id)
        ):
            row.username, row.display_name = None, "😀" * 100
        for index in range(3, 13):
            participant = Participant(
                campaign_id=campaign_id,
                user_id=300 + index,
                username=None,
                display_name="😀" * 100,
                private_reachable=True,
                qualification_state=QualificationState.ELIGIBLE,
                registered_revision=1,
            )
            session.add(participant)
            await session.flush()
            session.add(_draw_check(participant.id, now, state=QualificationState.ELIGIBLE))
            session.add(_ticket(campaign_id, participant.id, campaign.cutoff_at, index=index))
    return campaign_id


async def test_large_draw_queues_one_durable_delivery_per_complete_page_and_is_idempotent(
    session_factory,
):
    now = datetime.now(UTC)
    campaign_id = await _large_campaign(session_factory, now)
    await _settle_and_draw(session_factory, campaign_id, now)
    await DrawingService(partial(UnitOfWork, session_factory)).run(campaign_id)
    async with session_factory() as session:
        outboxes = list(await session.scalars(select(TelegramOutbox)))
    results = sorted(
        (row for row in outboxes if row.operation == "send_draw_result"),
        key=lambda row: row.payload["page_number"],
    )
    assert len(results) > 1
    assert len({row.business_key for row in results}) == len(results)
    winner_ids = [item["user_id"] for row in results for item in row.payload["winners"]]
    assert len(winner_ids) == len(set(winner_ids)) == 12
    assert all(
        html_utf16_length(draw_result_text(row.payload)) <= MESSAGE_UTF16_LIMIT for row in results
    )
    _assert_creator_delivery(results, outboxes)
    assert len([row for row in outboxes if row.operation == "send_winner_notice"]) == 12
    notices = [row.payload for row in outboxes if row.operation == "send_winner_notice"]
    assert all(row["issuer_user_id"] == 101 for row in notices)


def _assert_creator_delivery(results, outboxes):
    assert 'href="tg://user?id=101"' in draw_result_text(results[0].payload)
    assert all(
        'href="tg://user?id=101"' not in draw_result_text(row.payload) for row in results[1:]
    )
    assert len([row for row in outboxes if row.operation == "send_creator_draw_notice"]) == 1


@pytest.mark.parametrize("claimed", [False, True])
async def test_claim_idempotency_does_not_skip_authorization(session_factory, claimed):
    now = datetime.now(UTC)
    campaign_id, _, _ = await _seed_case(session_factory, now, winner_count=1)
    await _settle_and_draw(session_factory, campaign_id, now)
    async with session_factory() as session, session.begin():
        winner = await session.scalar(select(Winner))
        winner_id = winner.id
        if claimed:
            winner.claim_state = PrizeClaimState.CLAIMED
    with pytest.raises(AuthorizationDenied):
        await WinnerService(partial(UnitOfWork, session_factory)).mark_claimed(winner_id, 999)


@pytest.mark.parametrize("recipient_matches", [True, False])
async def test_pending_old_winner_notice_resolves_issuer_from_its_real_winner(
    session_factory, recipient_matches
):
    now = datetime.now(UTC)
    campaign_id, _, _ = await _seed_case(session_factory, now, winner_count=1)
    await _settle_and_draw(session_factory, campaign_id, now)
    async with session_factory() as session:
        original = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_winner_notice")
        )
        legacy = TelegramOutbox(
            operation=original.operation,
            aggregate_type=original.aggregate_type,
            aggregate_id=original.aggregate_id,
            recipient_chat_id=original.recipient_chat_id if recipient_matches else 999,
            payload={
                key: value
                for key, value in original.payload.items()
                if key not in {"issuer_user_id", "issuer_username"}
            },
        )
    if not recipient_matches:
        with pytest.raises(ValueError, match="recipient does not match"):
            await build_gateway_request(session_factory, legacy)
        return
    request = await build_gateway_request(session_factory, legacy)
    assert request.payload["issuer_user_id"] == 101
    assert "issuer_user_id" not in legacy.payload
    assert request.recipient_chat_id == original.recipient_chat_id
