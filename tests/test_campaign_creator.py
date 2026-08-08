from types import SimpleNamespace
from unittest.mock import AsyncMock

from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.telegram.creator_renderer import (
    TOKEN_LIFETIME,
    CreatorRenderer,
    PanelTarget,
)


async def test_creator_buttons_match_the_draft_lifetime() -> None:
    tokens = SimpleNamespace(create_many=AsyncMock(return_value=("token",)))
    renderer = CreatorRenderer(
        bot=SimpleNamespace(),
        tokens=tokens,
        queries=SimpleNamespace(),
    )
    target = PanelTarget(user_id=201, chat_id=300, message_id=400)
    requests = (CallbackRequest("draft_publish"),)

    assert await renderer._create_tokens(target, requests) == ("token",)
    tokens.create_many.assert_awaited_once_with(
        owner_user_id=201,
        chat_id=300,
        requests=requests,
        expires_in=TOKEN_LIFETIME,
    )
