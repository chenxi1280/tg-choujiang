from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.v30_helpers import FIRST, SECOND, START, rules_for, seed_v3
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    InviteLink,
    TelegramOutbox,
)
from tg_lottery.telegram import member_invites
from tg_lottery.telegram.gateway_requests import build_gateway_call


@pytest.fixture(autouse=True)
def campaign_clock(monkeypatch):
    # Delivery and the shared deadline view must observe the same fixed time.
    monkeypatch.setattr("tg_lottery.telegram.member_bonus_text.utcnow", lambda: START)


@pytest.mark.parametrize(
    ("mode", "bonuses", "chat_id", "expected"),
    [
        ("invite", (), FIRST, "完成邀请门槛"),
        ("invite", ("invite",), FIRST, "完成邀请门槛并增加邀请加成票"),
        ("message", ("invite",), FIRST, "增加邀请加成票"),
        ("invite", ("invite",), SECOND, "增加邀请加成票"),
    ],
)
async def test_ready_delivery_describes_the_actual_group_purpose(
    session_factory, monkeypatch, *, mode, bonuses, chat_id, expected
):
    rules = rules_for(mode, bonuses)
    if chat_id == SECOND:
        rules = rules.model_copy(
            update={"group_invite_rules": {str(SECOND): rules.group_invite_rules[str(FIRST)]}}
        )
    campaign_id, participant_id = await seed_v3(session_factory, rules)
    outbox = await queued_delivery(
        session_factory, campaign_id=campaign_id, participant_id=participant_id, chat_id=chat_id
    )
    monkeypatch.setattr(member_invites, "utcnow", lambda: START)
    call = await build_gateway_call(session_factory, outbox)
    note = call.request.payload["invite_delivery_note"]
    assert "邀请用途" in note and expected in note
    assert "最晚有效入群" in note and "本场有效邀请人数" in note
    assert call.request.payload["invite_bonus_active"] is True
    if expected == "完成邀请门槛":
        assert "加成" not in note
    if expected == "增加邀请加成票":
        assert "门槛" not in note


@pytest.mark.parametrize("phase", ["preheat", "deadline"])
async def test_gate_only_invite_delivery_inactive_phases_do_not_claim_bonus(
    session_factory, monkeypatch, phase
):
    campaign_id, participant_id = await seed_v3(session_factory, rules_for("invite", ()))
    outbox = await queued_delivery(
        session_factory, campaign_id=campaign_id, participant_id=participant_id, chat_id=FIRST
    )
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        if phase == "preheat":
            campaign.state = CampaignState.SCHEDULED
            campaign.preheat_at = START + timedelta(hours=1)
        else:
            campaign.cutoff_at = START - timedelta(minutes=1)
    monkeypatch.setattr(member_invites, "utcnow", lambda: START)
    call = await build_gateway_call(session_factory, outbox)
    assert call.request.payload["invite_bonus_active"] is False
    assert "加成" not in call.request.payload["invite_delivery_note"]


async def queued_delivery(factory, *, campaign_id, participant_id, chat_id):
    async with factory() as session, session.begin():
        target_id = await session.scalar(
            select(CampaignParticipationGroup.id).where(
                CampaignParticipationGroup.campaign_id == campaign_id,
                CampaignParticipationGroup.chat_id_snapshot == chat_id,
            )
        )
        link = InviteLink(
            campaign_id=campaign_id,
            participant_id=participant_id,
            participation_group_id=target_id,
            link_hash="f" * 64,
            encrypted_url=b"ciphertext",
            telegram_name="purpose_test",
        )
        session.add(link)
        await session.flush()
        outbox = TelegramOutbox(
            operation="send_invite_link",
            aggregate_type="invite_link",
            aggregate_id=str(link.id),
            recipient_chat_id=201,
            business_key=f"send_invite_link:{link.id}:201",
            request_hash="a" * 64,
            payload={"invite_link_id": str(link.id)},
            state=OutboxState.PENDING,
            run_at=START,
        )
        session.add(outbox)
        await session.flush()
        session.expunge(outbox)
    return outbox
