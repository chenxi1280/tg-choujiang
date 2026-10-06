from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import User
from sqlalchemy import func, select

from tests.test_registration import _request, _result
from tests.v30_helpers import FIRST, START, rules_for, seed_v3
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import (
    Campaign,
    Group,
    GroupAdmin,
    Participant,
    TelegramOutbox,
    TicketLedger,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.presentation.participation_rules import public_rule_blocks
from tg_lottery.telegram.draft_config import campaign_input, new_draft
from tg_lottery.telegram.member_activity_landing import open_activity
from tg_lottery.telegram.member_flow import MemberFlow


async def test_rules_landing_and_group_join_do_not_enroll(session_factory):
    cid, _ = await seed_v3(session_factory, rules_for("message"))
    participants = ParticipantService(partial(UnitOfWork, session_factory))
    queries = QueryService(session_factory)
    campaign = await queries.campaign(cid)
    flow = MemberFlow(
        bot=object(),
        participants=participants,
        queries=queries,
        cipher=object(),
        bot_username="test_bot",
    )
    message = SimpleNamespace(answer=AsyncMock())
    await open_activity(flow, message, campaign.public_ref)
    assert "参与条件" in message.answer.call_args.args[0]
    assert "我已阅读" in message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].text
    await flow.membership_changed(
        campaign_id=cid,
        user=User(id=202, is_bot=False, first_name="测试"),
        chat_id=FIRST,
        is_member=True,
        observed_at=START,
    )
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count(Participant.id)).where(Participant.user_id == 202)
            )
            == 0
        )
        assert await session.scalar(select(func.count(TicketLedger.id))) == 1


async def test_first_eligible_registration_is_permanent_and_one_base_ticket(session_factory):
    cid, _ = await seed_v3(session_factory, rules_for("message"))
    service = ParticipantService(partial(UnitOfWork, session_factory))
    with bind_update_context(1, 101):
        attempt = await service.begin_registration(_request(cid, START))
        await service.complete_registration(_result(attempt, START, QualificationState.UNKNOWN))
    async with session_factory() as session:
        participant = await session.get(Participant, attempt.participant_id)
        assert participant.registration_intent_at == START
        assert participant.counting_started_at is None and participant.ticket_total_cache == 0
    qualified_at = START + timedelta(seconds=1)
    for update_id, at in [(102, qualified_at), (103, START + timedelta(days=1))]:
        with bind_update_context(1, update_id):
            retry = await service.begin_registration(_request(cid, at))
            await service.complete_registration(_result(retry, at, QualificationState.ELIGIBLE))
            await service.complete_registration(_result(retry, at, QualificationState.ELIGIBLE))
    async with session_factory() as session:
        participant = await session.get(Participant, attempt.participant_id)
        assert participant.counting_started_at == qualified_at
        assert participant.counting_started_update_id == 102
        assert participant.registration_intent_at == START
        assert participant.ticket_total_cache == 1 and not participant.admission_qualified
        assert (
            await session.scalar(
                select(func.count(TicketLedger.id)).where(
                    TicketLedger.participant_id == participant.id
                )
            )
            == 1
        )


@pytest.mark.parametrize("mode", ["message", "invite"])
@pytest.mark.parametrize("bonuses", [(), ("activity",), ("invite",), ("activity", "invite")])
async def test_v3_creator_persists_frozen_rules_and_complete_publication_text(
    session_factory, mode, bonuses
):
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        group = Group(chat_id=FIRST, title="发言验收群")
        session.add(group)
        await session.flush()
        session.add(GroupAdmin(group_id=group.id, user_id=101, source="owner", active=True))
    configured = rules_for(mode, bonuses).model_dump(mode="json")
    data = new_draft(chat_id=FIRST) | {
        "title": "新规则创建链路",
        "draw_at": (now + timedelta(days=2)).isoformat(),
        "admission": configured["admission"],
        "bonus_types": list(bonuses),
        "invite_retention_hours": 0,
        "group_invite_rules": configured["group_invite_rules"],
        "group_activity_rules": configured["group_activity_rules"],
        "participation_groups": [
            {
                "id": FIRST,
                "title": "发言验收群",
                "type": "supergroup",
                "join_url": "https://t.me/test_group",
            }
        ],
        "prizes": [{"name": "测试占位", "winner_count": 1}],
    }
    draft, prizes = campaign_input(data, now=now)
    service = CampaignService(partial(UnitOfWork, session_factory))
    campaign = await service.create_draft(draft, prizes, 101)
    await service.publish(campaign.id, 101, "test_bot")
    async with session_factory() as session:
        frozen = await session.get(Campaign, campaign.id)
        assert frozen.contract_version == "v2.4"
        assert frozen.rules["participation_rules_version"] == "v3.0"
        outbox = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_campaign_card")
        )
        text = outbox.payload["rendered_card_text"]
        expected = "有效发言满 <b>2</b> 条" if mode == "message" else "有效邀请满 <b>2</b> 人"
        assert expected in text and "必须全部满足" in text
        assert "https://t.me/test_group" in text
        assert f"start=join_{frozen.public_ref}" in text
        assert f"start=rules_{frozen.public_ref}" in text
        full_rules = "\n".join(public_rule_blocks(outbox.payload))
        assert ("每天 2 条" if mode == "message" else "新成员 2 人") in full_rules
        assert outbox.payload["rules"] == frozen.rules
