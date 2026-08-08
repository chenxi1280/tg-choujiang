from types import SimpleNamespace
from unittest.mock import AsyncMock

from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.telegram.campaign_creator import (
    CREATOR_TOKEN_LIFETIME,
    CampaignCreator,
)


async def test_creator_buttons_match_the_draft_lifetime() -> None:
    tokens = SimpleNamespace(create_many=AsyncMock(return_value=("token",)))
    creator = CampaignCreator(
        bot=SimpleNamespace(),
        bot_username="lottery_bot",
        sessions=SimpleNamespace(),
        tokens=tokens,
        campaigns=SimpleNamespace(),
        queries=SimpleNamespace(),
    )
    message = SimpleNamespace(chat=SimpleNamespace(id=300))
    requests = (CallbackRequest("draft_publish"),)

    assert await creator._create_tokens(message, 201, requests) == ("token",)
    tokens.create_many.assert_awaited_once_with(
        owner_user_id=201,
        chat_id=300,
        requests=requests,
        expires_in=CREATOR_TOKEN_LIFETIME,
    )
