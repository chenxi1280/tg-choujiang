# ruff: noqa: RUF001

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, User

from tg_lottery.application.participant_records import (
    ParticipantCampaignDetail,
    ParticipatedCampaign,
    ParticipatedCampaignPage,
)
from tg_lottery.application.participant_results import ParticipantAward, ParticipantDrawResult
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.member_callbacks import record_callback
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.member_records import show_ticket_record
from tg_lottery.telegram.participant_list_views import build_participated_campaign_list
from tg_lottery.telegram.personal_flow import PersonalFlow
from tg_lottery.telegram.update_handler import LotteryUpdateHandler

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
USER_ID = 201
BOT_ID = 9001
GROUP_ID = -100123
PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"


async def test_group_progress_button_sends_result_only_to_clicking_users_private_chat():
    transport = AsyncMock(return_value=True)
    flow, queries = _flow(transport)
    query = _query()

    assert await record_callback(flow, None, query, data=f"member:tickets:{PUBLIC_REF}")

    requests = _sent_messages(transport)
    assert requests and all(request.chat_id == USER_ID for request in requests)
    assert "已中奖" in requests[0].text and "领奖方式：" in requests[0].text
    queries.participant_campaign_detail.assert_awaited_once_with(USER_ID, PUBLIC_REF)
    assert query.message.chat.id == GROUP_ID
    assert query.message.from_user.id == BOT_ID
    assert isinstance(transport.call_args.args[1], AnswerCallbackQuery)
    assert not transport.call_args.args[1].show_alert


async def test_real_handler_sends_private_result_before_acknowledging_group_button():
    transport = AsyncMock(return_value=True)
    flow, queries = _flow(transport)
    handler = _handler(flow, queries)
    query = _query().as_(flow._bot)

    await handler._callback(query)

    requests = [call.args[1] for call in transport.call_args_list]
    assert len(requests) == 2
    assert isinstance(requests[0], SendMessage) and requests[0].chat_id == USER_ID
    assert isinstance(requests[1], AnswerCallbackQuery) and not requests[1].show_alert
    assert "已中奖" in requests[0].text
    queries.participant_campaign_detail.assert_awaited_once_with(USER_ID, PUBLIC_REF)


async def test_real_handler_reports_private_forbidden_without_a_prior_success_ack():
    error = TelegramForbiddenError(
        method=SendMessage(chat_id=USER_ID, text="result"), message="Forbidden: bot blocked"
    )

    async def reject_message(bot, method, **kwargs):
        if isinstance(method, SendMessage):
            raise error
        return True

    transport = AsyncMock(side_effect=reject_message)
    flow, queries = _flow(transport)

    await _handler(flow, queries)._callback(_query().as_(flow._bot))

    requests = [call.args[1] for call in transport.call_args_list]
    assert len(requests) == 2
    assert isinstance(requests[0], SendMessage) and requests[0].chat_id == USER_ID
    assert isinstance(requests[1], AnswerCallbackQuery) and requests[1].show_alert
    assert "私聊发送失败" in requests[1].text


@pytest.mark.parametrize("private_forbidden", [False, True])
async def test_expired_ack_is_logged_without_replaying_private_send(private_forbidden, caplog):
    async def expired_ack(bot, method, **kwargs):
        if isinstance(method, SendMessage) and private_forbidden:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot blocked")
        if isinstance(method, AnswerCallbackQuery):
            raise TelegramBadRequest(method=method, message="query is too old")
        return True

    transport = AsyncMock(side_effect=expired_ack)
    flow, queries = _flow(transport)

    await _handler(flow, queries)._callback(_query().as_(flow._bot))

    assert len(_sent_messages(transport)) == 1
    requests = [call.args[1] for call in transport.call_args_list]
    assert len(requests) == 2 and isinstance(requests[1], AnswerCallbackQuery)
    assert requests[1].show_alert == private_forbidden
    assert "acknowledgement rejected by Telegram" in caplog.text
    if private_forbidden:
        assert "private delivery forbidden" in caplog.text


async def test_direct_member_flow_ticket_call_also_routes_group_to_private():
    transport = AsyncMock(return_value=True)
    flow, _ = _flow(transport)

    await flow.show_tickets(_query().message, USER_ID, public_ref=PUBLIC_REF)

    assert transport.call_args.args[1].chat_id == USER_ID


async def test_unreachable_private_result_reports_failure_and_never_posts_to_group(caplog):
    error = TelegramForbiddenError(
        method=SendMessage(chat_id=USER_ID, text="result"), message="Forbidden: bot blocked"
    )

    async def reject_message(bot, method, **kwargs):
        if isinstance(method, SendMessage):
            raise error
        return True

    transport = AsyncMock(side_effect=reject_message)
    flow, _ = _flow(transport)
    query = _query().as_(flow._bot)

    assert await record_callback(flow, None, query, data=f"member:tickets:{PUBLIC_REF}")

    requests = [call.args[1] for call in transport.call_args_list]
    assert len(requests) == 2
    assert isinstance(requests[0], SendMessage) and requests[0].chat_id == USER_ID
    assert isinstance(requests[1], AnswerCallbackQuery) and requests[1].show_alert
    assert "私聊发送失败" in requests[1].text and "/join" in requests[1].text
    assert "private delivery forbidden" in caplog.text


@pytest.mark.parametrize("error_type", [TelegramBadRequest, TelegramNetworkError])
async def test_other_private_send_failures_propagate_without_claiming_unreachable(error_type):
    error = error_type(method=SendMessage(chat_id=USER_ID, text="result"), message="send failed")
    transport = AsyncMock(side_effect=error)
    flow, _ = _flow(transport)

    with pytest.raises(error_type, match="send failed"):
        await record_callback(flow, None, _query(), data=f"member:tickets:{PUBLIC_REF}")

    assert transport.await_count == 1
    assert isinstance(transport.call_args.args[1], SendMessage)
    assert transport.call_args.args[1].chat_id == USER_ID


async def test_group_topic_and_business_context_are_removed_from_private_send():
    transport = AsyncMock(return_value=True)
    flow, _ = _flow(transport)
    query = _query()
    query = query.model_copy(
        update={
            "message": query.message.model_copy(
                update={
                    "is_topic_message": True,
                    "message_thread_id": 987,
                    "business_connection_id": "original-group-business-context",
                }
            )
        }
    )

    assert await record_callback(flow, None, query, data=f"member:tickets:{PUBLIC_REF}")

    request = _sent_messages(transport)[0]
    assert isinstance(request, SendMessage) and request.chat_id == USER_ID
    assert request.message_thread_id is None
    assert request.business_connection_id is None
    assert request.direct_messages_topic_id is None
    assert request.reply_to_message_id is None and request.reply_parameters is None
    assert query.message.message_thread_id == 987
    assert query.message.business_connection_id == "original-group-business-context"


@pytest.mark.parametrize("callback", ["member:tickets", "member:invites"])
async def test_legacy_record_buttons_open_the_clicking_users_private_list(callback):
    transport = AsyncMock(return_value=True)
    flow, queries = _flow(transport)
    queries.participated_campaign_page = AsyncMock(
        return_value=ParticipatedCampaignPage("active", 0, 1, ())
    )
    personal = PersonalFlow(profiles=None, queries=queries, bot_username="lottery_bot")
    query = _query()

    assert await record_callback(flow, personal, query, data=callback)

    assert queries.participated_campaign_page.call_args.args[0] == USER_ID
    assert _sent_messages(transport)[0].chat_id == USER_ID
    assert query.message.from_user.id == BOT_ID


async def test_invitation_record_callback_uses_private_message_and_clicking_user():
    flow, _ = _flow(AsyncMock(return_value=True))
    flow.show_invitations = AsyncMock()

    assert await record_callback(flow, None, _query(), data=f"member:invites:{PUBLIC_REF}:0")

    message, user_id = flow.show_invitations.call_args.args
    assert message.chat.type == "private" and message.chat.id == USER_ID
    assert message.from_user.id == USER_ID and user_id == USER_ID


@pytest.mark.parametrize("chat_id,chat_type", [(GROUP_ID, "supergroup"), (USER_ID + 1, "private")])
async def test_low_level_result_renderer_rejects_nonpersonal_destinations(chat_id, chat_type):
    query = _query(chat_id=chat_id, chat_type=chat_type)
    queries = SimpleNamespace(participant_campaign_detail=AsyncMock(return_value=_detail()))

    with pytest.raises(UserFacingError, match="本人私聊"):
        await show_ticket_record(query.message, queries, USER_ID, public_ref=PUBLIC_REF)

    queries.participant_campaign_detail.assert_not_awaited()


async def test_foreign_private_record_callback_cannot_read_another_users_result():
    transport = AsyncMock(return_value=True)
    flow, queries = _flow(transport)

    with pytest.raises(UserFacingError, match="本人私聊"):
        await record_callback(
            flow,
            None,
            _query(chat_id=USER_ID + 1, chat_type="private"),
            data=f"member:tickets:{PUBLIC_REF}",
        )

    queries.participant_campaign_detail.assert_not_awaited()
    transport.assert_not_awaited()


@pytest.mark.parametrize("state", [CampaignState.DRAWN, CampaignState.ARCHIVED])
async def test_ended_list_explains_unqualified_history_without_stashed_promise(state):
    row = _campaign_row(state, qualified=False)
    text, keyboard = build_participated_campaign_list(
        ParticipatedCampaignPage("ended", 0, 1, (row,)), invitation_only=False
    )

    assert "历史累计 3 次" in text and "未计入本场" in text
    assert "暂存" not in text
    assert "暂存" not in keyboard.inline_keyboard[1][0].text


@pytest.mark.parametrize("qualified", [False, True])
async def test_cancelled_list_never_promises_tickets_can_still_become_effective(qualified):
    row = _campaign_row(CampaignState.CANCELLED, qualified=qualified)
    text, _ = build_participated_campaign_list(
        ParticipatedCampaignPage("ended", 0, 1, (row,)), invitation_only=False
    )

    assert "历史累计 3 次" in text and "活动已取消" in text
    assert "暂存" not in text and "未计入本场" not in text


def test_drawn_qualified_history_points_to_final_snapshot_count():
    row = _campaign_row(CampaignState.DRAWN, qualified=True)
    text, _ = build_participated_campaign_list(
        ParticipatedCampaignPage("ended", 0, 1, (row,)), invitation_only=False
    )

    assert "历史累计 3 次" in text and "最终次数见详情" in text


def _flow(transport):
    bot = Bot(token="123456:test-token", session=transport)
    queries = SimpleNamespace(participant_campaign_detail=AsyncMock(return_value=_detail()))
    flow = MemberFlow(bot=bot, participants=None, queries=queries, cipher=None)
    return flow, queries


def _sent_messages(transport):
    return [
        call.args[1] for call in transport.call_args_list if isinstance(call.args[1], SendMessage)
    ]


def _query(*, chat_id=GROUP_ID, chat_type="supergroup"):
    return CallbackQuery(
        id="record-button",
        from_user=User(id=USER_ID, is_bot=False, first_name="参与人"),
        chat_instance="records",
        data=f"member:tickets:{PUBLIC_REF}",
        message=Message(
            message_id=8,
            date=NOW,
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=BOT_ID, is_bot=True, first_name="Lottery"),
        ),
    )


def _handler(flow, queries):
    return LotteryUpdateHandler(
        bot=flow._bot,
        default_timezone="Asia/Shanghai",
        admin_flow=None,
        member_flow=flow,
        personal_flow=PersonalFlow(profiles=None, queries=queries, bot_username="lottery_bot"),
        tokens=None,
        groups=None,
        activity=None,
        invitations=None,
        queries=queries,
        stats=None,
    )


def _detail():
    result = ParticipantDrawResult(
        NOW, 3, (ParticipantAward(1, "一等奖", 1, "私聊发奖人领取"),), "发奖人", 301, None
    )
    return ParticipantCampaignDetail(
        public_ref=PUBLIC_REF,
        campaign_name="测试活动",
        state=CampaignState.DRAWN,
        qualification=QualificationState.ELIGIBLE,
        total=3,
        registration=3,
        activity=0,
        invitation=0,
        bonus_enabled=False,
        invite_enabled=False,
        draw_at=NOW,
        timezone="Asia/Shanghai",
        draw_result=result,
    )


def _campaign_row(state, *, qualified):
    return ParticipatedCampaign(
        public_ref=PUBLIC_REF,
        name="测试活动",
        state=state,
        qualification=QualificationState.ELIGIBLE,
        tickets=3,
        draw_at=NOW,
        timezone="Asia/Shanghai",
        base_qualified=qualified,
    )
