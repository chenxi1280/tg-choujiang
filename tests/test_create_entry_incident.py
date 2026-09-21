from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy import func, select

from tests.test_update_handler import _handler
from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackTokenService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_renderer import CreatorRenderer, PanelTarget

USER_ID = 201
PANEL_ID = 381


def _query():
    user = User(id=USER_ID, is_bot=False, first_name="Creator")
    return CallbackQuery(
        id="create",
        data="nav:create",
        from_user=user,
        chat_instance="private",
        message=Message(
            message_id=PANEL_ID,
            date=datetime.now(UTC),
            chat=Chat(id=USER_ID, type="private"),
            from_user=user,
        ),
    )


@pytest.fixture
def creator_case(session_factory, monkeypatch):
    factory = partial(UnitOfWork, session_factory)
    sessions = AdminSessionService(factory)
    tokens = CallbackTokenService(factory)
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    queries = QueryService(session_factory)
    edits = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edits)
    answers = AsyncMock(return_value=_query().message)
    markup_edits = AsyncMock()
    monkeypatch.setattr(Message, "answer", answers)
    monkeypatch.setattr(Message, "edit_reply_markup", markup_edits)
    renderer = CreatorRenderer(bot=bot, sessions=sessions, tokens=tokens, queries=queries)
    creator = CampaignCreator(
        bot=bot,
        bot_username="lottery_test_bot",
        sessions=sessions,
        campaigns=object(),
        queries=queries,
        renderer=renderer,
        settings=object(),
        profiles=object(),
    )
    return SimpleNamespace(
        creator=creator,
        sessions=sessions,
        tokens=tokens,
        bot=bot,
        edits=edits,
        answers=answers,
        markup_edits=markup_edits,
        renderer=renderer,
    )


@pytest.mark.parametrize("entry", ["button", "command"])
async def test_zero_group_creator_opens_saved_draft_with_working_buttons(creator_case, entry):
    case = creator_case
    query = _query()
    if entry == "button":
        await case.creator.start(query)
    else:
        await case.creator.start_message(query.message)
    stored = await case.sessions.get(USER_ID)
    assert stored.state == "draft:idle" and stored.data["chat_id"] is None
    text_call = case.edits.call_args if entry == "button" else case.answers.call_args
    markup_call = case.edits.call_args if entry == "button" else case.markup_edits.call_args
    assert "抽奖预览" in text_call.args[0]
    assert "活动群" in text_call.args[0]
    keyboard = markup_call.kwargs["reply_markup"]
    button = next(
        item for row in keyboard.inline_keyboard for item in row if item.text == "活动标题"
    )
    grant = await case.tokens.consume(
        button.callback_data.removeprefix("secure:"), owner_user_id=USER_ID, chat_id=USER_ID
    )
    await case.creator.handle_grant(query, grant)
    assert (await case.sessions.get(USER_ID)).state == "draft:title"
    assert "请回复抽奖标题" in case.edits.call_args.args[0]


async def test_zero_group_reopening_preserves_configured_draft(creator_case):
    case = creator_case
    await case.creator.start(_query())
    first = await case.sessions.get(USER_ID)
    await case.sessions.set(USER_ID, "draft:idle", first.data | {"title": "已经编辑的草稿"})
    await case.creator.start(_query())
    continued = await case.sessions.get(USER_ID)
    assert continued.data["draft_id"] == first.data["draft_id"]
    assert continued.data["title"] == "已经编辑的草稿"


async def test_zero_group_list_explains_setup_and_has_only_usable_navigation(creator_case):
    case = creator_case
    await case.creator.start(_query())
    await case.renderer.edit_groups(PanelTarget(USER_ID, USER_ID, PANEL_ID))
    assert "当前没有可管理的群" in case.bot.edit_message_text.call_args.args[0]
    assert "你和机器人都是该群管理员" in case.bot.edit_message_text.call_args.args[0]
    rows = case.bot.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert [button.text for row in rows for button in row] == ["🔄 刷新群列表", "↩ 返回上一层"]


async def test_zero_group_publish_is_rejected_without_campaign_creation(
    creator_case, session_factory
):
    case = creator_case
    await case.creator.start(_query())
    original = (await case.sessions.get(USER_ID)).data
    with pytest.raises(UserFacingError, match="活动群"):
        await case.creator._publisher.confirm(_query(), object())
    assert (await case.sessions.get(USER_ID)).data == original
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Campaign)) == 0


@pytest.mark.parametrize(
    "failure", [UserFacingError("请先完成: 活动群"), ValueError("private detail")]
)
async def test_private_callback_errors_send_visible_message_after_ack(monkeypatch, failure):
    handler = _handler()
    handler._dispatch_callback = AsyncMock(side_effect=failure)
    answer = AsyncMock()
    notification = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(Message, "answer", notification)
    await handler._callback(_query())
    answer.assert_awaited_once_with()
    notification.assert_awaited_once()
    text = notification.call_args.args[0]
    assert text.startswith("操作未完成:")
    assert "private detail" not in text
    if isinstance(failure, UserFacingError):
        assert "活动群" in text
