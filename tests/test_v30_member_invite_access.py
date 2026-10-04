from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.v30_helpers import FIRST, rules_for, seed_v3
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.member_base_activity import base_registration_reply
from tg_lottery.telegram.member_callbacks import (
    bonus_callback,
    bonus_category_callback,
    bonus_group_detail_callback,
    group_invite_callback,
    show_bonus_message,
)
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.member_records import show_ticket_record


async def member_flow(factory, bonuses, *, mode="invite"):
    campaign_id, _participant_id = await seed_v3(factory, rules_for(mode, bonuses))
    bot = SimpleNamespace(
        id=9001,
        send_message=AsyncMock(),
        get_chat=AsyncMock(
            return_value=SimpleNamespace(permissions=SimpleNamespace(can_send_messages=True))
        ),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status="administrator", can_invite_users=True)
        ),
    )
    queries = QueryService(factory)
    flow = MemberFlow(
        bot=bot,
        participants=ParticipantService(partial(UnitOfWork, factory)),
        queries=queries,
        cipher=object(),
        bot_username="test_bot",
    )
    message = SimpleNamespace(chat=SimpleNamespace(type="private", id=201), answer=AsyncMock())
    return flow, await queries.campaign(campaign_id), message


@pytest.mark.parametrize("bonuses", [(), ("activity",), ("invite",), ("activity", "invite")])
async def test_invite_gate_menu_to_personal_link_request_without_bonus_dependency(
    session_factory, bonuses
):
    flow, campaign, message = await member_flow(session_factory, bonuses)
    await base_registration_reply(
        message, campaign, user_id=201, callback=None, queries=flow._queries
    )
    expected = "我的加成" if bonuses else "邀请门槛与入口"
    assert expected in button_labels(message)
    await show_ticket_record(message, flow._queries, 201, public_ref=campaign.public_ref)
    assert expected in button_labels(message)
    await show_bonus_message(flow, message, campaign.public_ref, user_id=201)
    query = SimpleNamespace(message=message, from_user=SimpleNamespace(id=201), answer=AsyncMock())
    await bonus_callback(flow, query, campaign.public_ref)
    assert flow._bot.send_message.call_args.args[0] == 201
    await bonus_category_callback(flow, query, f"{campaign.public_ref}:0", category="invites")
    details = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    assert any(
        button.callback_data == f"m:id:{campaign.public_ref}:{FIRST}:0"
        for row in details
        for button in row
    )
    await bonus_group_detail_callback(
        flow, query, f"{campaign.public_ref}:{FIRST}:0", category="invites"
    )
    assert "邀请门槛" in message.answer.call_args.args[0]
    assert f"member:invite_group:{campaign.public_ref}:{FIRST}" in button_callbacks(message)
    await group_invite_callback(flow, query, f"{campaign.public_ref}:{FIRST}")
    async with session_factory() as session:
        requests = list(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.operation == "create_invite_link")
            )
        )
    assert len(requests) == 1 and requests[0].recipient_chat_id == FIRST


async def test_message_gate_without_bonuses_does_not_offer_invite_tools(session_factory):
    flow, campaign, message = await member_flow(session_factory, (), mode="message")
    await base_registration_reply(
        message, campaign, user_id=201, callback=None, queries=flow._queries
    )
    assert "邀请门槛与入口" not in button_labels(message)
    with pytest.raises(UserFacingError, match="没有开启加成"):
        await show_bonus_message(flow, message, campaign.public_ref, user_id=201)


async def test_invite_gate_tools_still_require_registration(session_factory):
    flow, campaign, message = await member_flow(session_factory, ())
    with pytest.raises(UserFacingError, match="完成全部参与群资格校验"):
        await show_bonus_message(flow, message, campaign.public_ref, user_id=202)


def button_labels(message):
    rows = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    return [button.text for row in rows for button in row]


def button_callbacks(message):
    rows = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    return [button.callback_data for row in rows for button in row]
