from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    Message,
    User,
)

from tg_lottery.application.participant_records import ParticipantCampaignDetail
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.member_navigation import MemberNavigation
from tg_lottery.telegram.member_navigation_context import MemberPageContext, _descriptor

USER_ID = 202
REFERENCE = "AbCdEfGhIjKlMnOpQrSt-_"


def _query(chat_id=USER_ID, *, kind="private"):
    return CallbackQuery(
        id="test",
        from_user=User(id=USER_ID, is_bot=False, first_name="Member"),
        chat_instance="test",
        message=Message(
            message_id=7,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type=kind),
            from_user=User(id=9001, is_bot=True, first_name="Bot"),
        ),
    )


def _detail():
    return ParticipantCampaignDetail(
        public_ref=REFERENCE,
        campaign_name="本人活动",
        state=CampaignState.RUNNING,
        qualification=QualificationState.INELIGIBLE,
        total=5,
        registration=1,
        activity=3,
        invitation=1,
        bonus_enabled=True,
        invite_enabled=True,
        draw_at=datetime.now(UTC),
        timezone="Asia/Shanghai",
        base_progress="本场已完成门槛",
        base_qualified=True,
    )


def _navigation():
    queries = SimpleNamespace(participant_campaign_detail=AsyncMock(return_value=_detail()))

    async def issue(**options):
        return tuple(f"token{index}" for index, _ in enumerate(options["requests"]))

    tokens = SimpleNamespace(create_many=AsyncMock(side_effect=issue))
    flow = MemberNavigation(
        bot=object(),
        queries=queries,
        cipher=object(),
        tokens=tokens,
        profiles=object(),
        bot_username="test_bot",
    )
    return flow, queries, tokens


def test_return_to_home_never_turns_into_return_to_previous_page():
    context = MemberPageContext("m:bo:" + REFERENCE, ("m:detail:" + REFERENCE,))
    home = _descriptor(InlineKeyboardButton(text="返回首页", callback_data="nav:index"), context)
    assert home["route"] == "nav:index"
    back = _descriptor(
        InlineKeyboardButton(text="返回活动详情", callback_data="member:tickets:" + REFERENCE),
        context,
    )
    assert back["route"] == "m:detail:" + REFERENCE
    assert back["trail"] == []


def test_record_filter_and_page_changes_do_not_add_a_fake_parent():
    context = MemberPageContext(f"m:ir:{REFERENCE}:-1001:2:all:0", (f"m:id:{REFERENCE}:-1001:2",))
    page = _descriptor(
        InlineKeyboardButton(text="下一页", callback_data=f"m:ir:{REFERENCE}:-1001:2:all:1"),
        context,
    )
    assert page["trail"] == list(context.trail)


def test_content_page_changes_keep_the_same_route_and_real_parent():
    context = MemberPageContext("m:detail:" + REFERENCE, ("nav:join:ended:3",))
    value = _descriptor(InlineKeyboardButton(text="下一页", callback_data="m:page:1"), context)
    assert value["route"] == context.route
    assert value["trail"] == ["nav:join:ended:3"]
    assert value["content_page"] == 1


async def test_ineligible_member_still_reads_their_record_without_new_registration(monkeypatch):
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    flow, queries, tokens = _navigation()
    edit, answer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Message, "answer", answer)
    assert await flow.handle_route(_query(), data="m:detail:" + REFERENCE, personal=object())
    queries.participant_campaign_detail.assert_awaited_once_with(USER_ID, REFERENCE)
    answer.assert_not_awaited()
    assert "5" in edit.await_args.args[0] and "0" in edit.await_args.args[0]
    assert tokens.create_many.await_args.kwargs["owner_user_id"] == USER_ID
    assert tokens.create_many.await_args.kwargs["chat_id"] == USER_ID


async def test_group_navigation_sends_private_record_and_never_edits_group_panel(monkeypatch):
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    flow, _, _ = _navigation()
    edit, answer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Message, "answer", answer)
    await flow.handle_route(
        _query(-100123, kind="supergroup"), data="m:detail:" + REFERENCE, personal=object()
    )
    edit.assert_not_awaited()
    answer.assert_awaited_once()


async def test_foreign_private_chat_does_not_receive_someone_elses_record(monkeypatch):
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    flow, _, _ = _navigation()
    edit, answer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    monkeypatch.setattr(Message, "answer", answer)
    with pytest.raises(UserFacingError, match="本人私聊"):
        await flow.handle_route(_query(303), data="m:detail:" + REFERENCE, personal=object())
    edit.assert_not_awaited()
    answer.assert_not_awaited()


async def test_private_delivery_forbidden_is_explicit_and_not_acknowledged_as_success(monkeypatch):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    flow, _, _ = _navigation()
    acknowledgement = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", acknowledgement)
    error = TelegramForbiddenError(
        method=SendMessage(chat_id=USER_ID, text="本人记录"),
        message="Forbidden: bot was blocked by the user",
    )
    monkeypatch.setattr(Message, "answer", AsyncMock(side_effect=error))
    with pytest.raises(UserFacingError, match="私聊暂不可达"):
        await flow.handle_route(
            _query(-100123, kind="supergroup"), data="m:detail:" + REFERENCE, personal=object()
        )
    acknowledgement.assert_not_awaited()
