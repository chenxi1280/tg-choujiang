from types import SimpleNamespace
from unittest.mock import AsyncMock

from tg_lottery.application.admin_sessions import DraftPanelContext
from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.telegram.creator_renderer import (
    TOKEN_LIFETIME,
    CreatorRenderer,
    PanelTarget,
)


async def test_creator_buttons_match_the_draft_lifetime() -> None:
    tokens = SimpleNamespace(create_many=AsyncMock(return_value=("token",)))
    context = DraftPanelContext("draft-1", 3, 400)
    sessions = SimpleNamespace(issue_panel_context=AsyncMock(return_value=context))
    renderer = CreatorRenderer(
        bot=SimpleNamespace(),
        tokens=tokens,
        queries=SimpleNamespace(),
        sessions=sessions,
    )
    target = PanelTarget(user_id=201, chat_id=300, message_id=400)
    requests = (CallbackRequest("draft_publish"),)

    assert await renderer._create_tokens(target, requests) == ("token",)
    sessions.issue_panel_context.assert_awaited_once_with(201, 400)
    tokens.create_many.assert_awaited_once_with(
        owner_user_id=201,
        chat_id=300,
        requests=(
            CallbackRequest(
                "draft_publish",
                payload={
                    "draft_id": "draft-1",
                    "draft_revision": 3,
                    "panel_message_id": 400,
                },
            ),
        ),
        expires_in=TOKEN_LIFETIME,
    )
