from datetime import UTC, datetime

from tests.helpers import seed_campaign
from tests.test_v23_referral_experience import _second_campaign, _target
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Group

PRIMARY_CHAT = -100123
OTHER_CHAT = -100124
MIGRATED_CHAT = -100125
MISSING_CHAT = -100126


async def test_group_page_counts_match_individual_queries_for_all_campaign_and_route_states(
    session_factory,
):
    async with session_factory() as session, session.begin():
        primary, first, _ = await seed_campaign(session)
        first.contract_version = "v2.3"
        other = Group(chat_id=OTHER_CHAT, title="另一个群", timezone="Asia/Shanghai")
        session.add(other)
        await session.flush()
        first_target = _target(first)
        secondary_target = _target(first)
        secondary_target.group_id = other.id
        secondary_target.chat_id_snapshot = OTHER_CHAT
        secondary_target.position = 2
        secondary_target.is_primary = False
        session.add_all((first_target, secondary_target))
        for state in CampaignState:
            campaign = _second_campaign(primary.id, CampaignRules(), datetime.now(UTC))
            campaign.state = state
            session.add(campaign)
            await session.flush()
            if state in {CampaignState.RUNNING, CampaignState.PUBLISHING}:
                session.add(_target(campaign))
        other.migrated_from_chat_id = OTHER_CHAT
        other.chat_id = MIGRATED_CHAT
    queries = QueryService(session_factory)
    chat_ids = (PRIMARY_CHAT, OTHER_CHAT, MIGRATED_CHAT, MISSING_CHAT)
    counts = await queries.active_campaign_counts_by_chats(chat_ids)
    for chat_id in chat_ids:
        assert counts.get(chat_id, 0) == len(await queries.active_campaigns_by_chat(chat_id))
    assert counts[OTHER_CHAT] == counts[MIGRATED_CHAT] == 1
    assert MISSING_CHAT not in counts
    assert await queries.active_campaign_counts_by_chats(()) == {}
