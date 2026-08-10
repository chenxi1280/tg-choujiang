from uuid import uuid4

from tests.helpers import seed_campaign
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.public_refs import is_campaign_public_ref


async def test_campaign_reference_resolves_public_and_legacy_protocols(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
        public_ref = campaign.public_ref
    queries = QueryService(session_factory)

    public_campaign = await queries.campaign_by_reference(public_ref)
    legacy_campaign = await queries.campaign_by_reference(campaign_id)

    assert is_campaign_public_ref(public_ref)
    assert public_campaign.id == campaign_id
    assert legacy_campaign.id == campaign_id
    assert await queries.campaign_by_reference("not-a-campaign-reference") is None
    assert await queries.campaign_by_reference(uuid4()) is None


async def test_username_candidates_are_case_insensitive_and_deduplicated(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, _, participant = await seed_campaign(session)
        participant.username = "Prize_Owner"
        user_id = participant.user_id

    queries = QueryService(session_factory)

    assert await queries.user_ids_by_username("@prize_owner") == (user_id,)
