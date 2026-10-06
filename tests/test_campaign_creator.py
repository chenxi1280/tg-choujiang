from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import User

from tg_lottery.application.admin_sessions import DraftPanelContext
from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.telegram.campaign_creator import CampaignCreator
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


async def test_new_campaign_opens_preview_dashboard_directly() -> None:
    sessions = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    group = SimpleNamespace(
        id="stable-test-group", chat_id=-1001, title="活动群", timezone="Asia/Shanghai"
    )
    queries = SimpleNamespace(admin_groups=AsyncMock(return_value=[group]))
    renderer = SimpleNamespace(render=AsyncMock())
    creator = CampaignCreator(
        bot=object(),
        bot_username="lottery_test_bot",
        sessions=sessions,
        campaigns=object(),
        queries=queries,
        renderer=renderer,
        settings=object(),
        profiles=object(),
    )
    user = User(id=201, is_bot=False, first_name="Creator")
    message = SimpleNamespace(message_id=77)

    await creator._open(user, message)

    assert sessions.set.await_args.args[1] == "draft:idle"
    data = sessions.set.await_args.args[2]
    assert data["chat_id"] == -1001 and data["prizes"] == []
    assert renderer.render.await_args.kwargs["text"].startswith("创建抽奖 · 编辑草稿")
