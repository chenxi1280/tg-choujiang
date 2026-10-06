from datetime import timedelta
from functools import partial
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from sqlalchemy import func, select

from tests.navigation_helpers import PassthroughAdmin, PassthroughNavigation
from tests.v22_bonus_test_helpers import draft_data
from tests.v30_helpers import FIRST, START, rules_for, seed_v3
from tg_lottery.application.activity import ActivityService
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import BaseActivityEvent, Participant, TicketLedger
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram import creator_behavior_editor as gate
from tg_lottery.telegram import creator_behavior_views as gate_views
from tg_lottery.telegram.creator_group_bonus import apply_action, editor_view
from tg_lottery.telegram.message_observation import observe_message
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


def _message(number, *, text=None, media=None, identity="first", **fields):
    payload = dict(
        message_id=number,
        date=START + timedelta(seconds=number),
        chat={"id": FIRST, "type": "supergroup"},
        from_user={"id": 201, "is_bot": False, "first_name": "测试"},
        text=text,
    )
    file = dict(file_id=f"download-{identity}", file_unique_id=identity, width=100, height=100)
    if media == "photo":
        payload[media] = [file | {"file_unique_id": "small"}, file]
    if media == "sticker":
        payload[media] = file | {"type": "regular", "is_animated": False, "is_video": False}
    if media == "animation":
        payload[media] = file | {"duration": 1}
    return Message.model_validate(payload | fields)


def _handler(factory):
    uow = partial(UnitOfWork, factory)
    return LotteryUpdateHandler(
        navigation=PassthroughNavigation(),
        bot=object(),
        default_timezone="Asia/Shanghai",
        admin_flow=PassthroughAdmin(),
        member_flow=object(),
        personal_flow=object(),
        tokens=object(),
        groups=object(),
        activity=ActivityService(uow),
        invitations=object(),
        queries=object(),
        stats=MessageStatsService(uow),
        bot_username="testbot",
    )


@pytest.mark.parametrize("media", ["photo", "sticker", "animation"])
async def test_media_advances_gate_bonus_and_total_with_replay_and_content_dedup(
    session_factory, media
):
    _cid, pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    handler = _handler(session_factory)
    messages = [
        _message(1, media=media),
        _message(2, media=media),  # 相同媒体的新消息仍受已有重复规则约束。
        _message(3, media=media, identity="second"),
    ]
    for message in [*messages, *messages]:
        await handler._message(message)
    assert await handler._stats.totals(201, (FIRST,)) == {FIRST: 2}
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified and participant.ticket_total_cache == 2
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 2
        assert await session.scalar(select(func.count(BaseActivityEvent.id))) == 3
        rejected = await session.scalar(
            select(BaseActivityEvent.rejection_reason).where(BaseActivityEvent.accepted.is_(False))
        )
        assert rejected == "duplicate_text_window"


async def test_short_text_and_emoji_complete_gate_and_award_bonus(session_factory):
    _cid, pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    handler = _handler(session_factory)
    for message in [_message(1, text="好"), _message(2, text="🎉")]:
        await handler._message(message)
    assert await handler._stats.totals(201, (FIRST,)) == {FIRST: 2}
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert participant.admission_qualified and participant.ticket_total_cache == 2
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 2


@pytest.mark.parametrize("media", ["photo", "sticker", "animation"])
def test_native_media_without_caption_is_not_a_service_message(media):
    observation = observe_message(_message(1, media=media))
    assert not observation.is_service
    assert observation.text is None and observation.media_key == f"{media}:first"


def test_service_and_forwarded_photo_keep_exclusion_facts():
    service = _message(1, new_chat_members=[{"id": 301, "is_bot": False, "first_name": "新成员"}])
    forwarded = _message(
        2,
        media="photo",
        forward_origin={
            "type": "user",
            "date": START,
            "sender_user": {"id": 301, "is_bot": False, "first_name": "原发送者"},
        },
    )
    assert observe_message(service).is_service
    assert observe_message(forwarded).is_forwarded


@pytest.mark.parametrize("kind", ["bot", "service", "forwarded", "command", "blank"])
async def test_excluded_messages_do_not_advance_gate_bonus_or_total(
    session_factory, kind, monkeypatch
):
    monkeypatch.setattr(Message, "answer", AsyncMock())
    _cid, pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    handler = _handler(session_factory)
    payloads = {
        "bot": dict(from_user={"id": 201, "is_bot": True, "first_name": "机器人"}),
        "service": dict(
            text=None,
            new_chat_members=[{"id": 301, "is_bot": False, "first_name": "新成员"}],
        ),
        "forwarded": dict(
            text=None,
            media="photo",
            forward_origin={
                "type": "user",
                "date": START,
                "sender_user": {"id": 301, "is_bot": False, "first_name": "原发送者"},
            },
        ),
        "command": dict(text="/start"),
        "blank": dict(text=" "),
    }
    await handler._message(_message(1, **(dict(text="好") | payloads[kind])))
    assert await handler._stats.totals(201, (FIRST,)) == {}
    async with session_factory() as session:
        participant = await session.get(Participant, pid)
        assert not participant.admission_qualified and participant.ticket_total_cache == 1
        assert await session.scalar(select(func.sum(TicketLedger.delta))) == 1


def test_creators_remove_length_controls_and_reject_old_length_callbacks():
    data = draft_data()
    editing = gate.apply_action(gate.open_editor(data), "draft_gate:mode:message")
    editing = gate.apply_action(editing, "draft_gate:group:-1001")
    text, rows = gate_views.view(editing)
    assert "文字、图片、表情均计数" in text
    assert "min_text_length" not in str(rows) and "字数" not in str(rows)
    with pytest.raises(UserFacingError, match="已失效"):
        gate.apply_action(editing, "draft_gate:input:min_text_length")
    bonus = apply_action(data, "draft_bonus_activity_group:-1001")
    text, rows = editor_view(bonus, bonus["bonus_working_copy"])
    assert "文字、图片、表情均计数" in text
    assert "length" not in str(rows) and "字数" not in str(rows)
    for action in ("draft_bonus_input_length", "draft_bonus_preset:length:20"):
        with pytest.raises(UserFacingError, match="字数设置已移除"):
            apply_action(bonus, action)
