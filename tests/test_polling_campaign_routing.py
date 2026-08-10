from datetime import UTC, datetime

from aiogram.types import Update

from tests.helpers import seed_campaign
from tg_lottery.telegram.polling import _routing_campaign_id


def _callback_update(update_id: int, campaign_reference: str) -> Update:
    timestamp = int(datetime.now(UTC).timestamp())
    return Update.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": 201, "is_bot": False, "first_name": "Tester"},
                "chat_instance": "private-chat",
                "data": f"member:verify:{campaign_reference}",
                "message": {
                    "message_id": update_id,
                    "date": timestamp,
                    "chat": {"id": 201, "type": "private"},
                    "from": {"id": 123, "is_bot": True, "first_name": "Lottery Bot"},
                },
            },
        }
    )


async def test_verify_callback_routes_public_and_legacy_refs_to_campaign(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
        public_ref = campaign.public_ref

    async with session_factory() as session:
        public_update = _callback_update(11, public_ref)
        legacy_update = _callback_update(12, str(campaign_id))
        assert await _routing_campaign_id(session, public_update) == str(campaign_id)
        assert await _routing_campaign_id(session, legacy_update) == str(campaign_id)
