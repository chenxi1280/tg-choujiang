from datetime import UTC, datetime
from functools import partial

import pytest

from tests.test_v33_platform_runtime import seed_platform
from tg_lottery.application.bot_relationships import BotRelationshipService
from tg_lottery.application.platform_review import relevant_inbox
from tg_lottery.domain.enums import QualificationState, UpdateState
from tg_lottery.infrastructure.db import Campaign, Participant, TelegramUpdate
from tg_lottery.infrastructure.db.uow import UnitOfWork


@pytest.mark.parametrize("bot_id", [999, 8763179035])
async def test_blocked_relationship_projects_qualification_for_bigint_bot(session_factory, bot_id):
    campaign_id, participant_id = await seed_platform(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.rules = campaign.rules | {
            "platform_policy": campaign.rules["platform_policy"] | {"bot_id": bot_id}
        }
        participant = await session.get(Participant, participant_id)
        participant.qualification_state = QualificationState.ELIGIBLE
    service = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=bot_id)
    await service.record(201, datetime.now(UTC), blocked=True)
    async with session_factory() as session:
        participant = await session.get(Participant, participant_id)
        assert participant.qualification_state is QualificationState.INELIGIBLE


@pytest.mark.parametrize("user_id", [201, 6758620028])
async def test_platform_inbox_selects_only_matching_bigint_member(session_factory, user_id):
    campaign_id, _ = await seed_platform(session_factory)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        for update_id, member_id in enumerate((user_id, user_id + 1), 10):
            session.add(
                TelegramUpdate(
                    bot_id=999,
                    update_id=update_id,
                    update_type="chat_member",
                    routing_chat_id=-10001,
                    event_at=now,
                    payload_hash=str(update_id) * 32,
                    state=UpdateState.RECEIVED,
                    payload={"chat_member": {"new_chat_member": {"user": {"id": member_id}}}},
                )
            )
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        rows = tuple(
            await session.scalars(relevant_inbox(campaign, (user_id,), chat_ids=(-10001,)))
        )
        assert [row.update_id for row in rows] == [10]
