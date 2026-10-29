from datetime import timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import GetChatMember
from aiogram.types import InlineKeyboardButton
from sqlalchemy import select

from tests.helpers import seed_campaign
from tests.test_r14_start_and_signup import _original_card
from tg_lottery.application.channel_shortcuts import queue_channel_shortcut
from tg_lottery.application.notice_recovery import (
    cancel_unauthorized_shortcut,
    retry_failed_notices,
)
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.action_confirmation import ExpectedCampaign
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import AuthorizationDenied, UserFacingError
from tg_lottery.infrastructure.db import Campaign, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.member_missing_targets import registration_failure_feedback
from tg_lottery.telegram.message_delete_permissions import (
    _rights,
    deletion_permission_text,
    inspect_deletion_permissions,
)
from tg_lottery.telegram.ui_style import row_keyboard, style_label


async def test_per_target_delete_permissions_keep_unknown_and_own_message_rights():
    bot = SimpleNamespace(
        id=901,
        get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup")),
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status="member"),
                SimpleNamespace(status="administrator", can_delete_messages=True),
                TelegramForbiddenError(
                    method=GetChatMember(chat_id=-3, user_id=901), message="blocked"
                ),
            ]
        ),
    )
    data = {
        "participation_groups": [
            {"id": -1, "title": "一群"},
            {"id": -2, "title": "二群"},
            {"id": -3, "title": "三群"},
        ],
        "delete_trigger_message": True,
    }
    result = await inspect_deletion_permissions(bot, data)
    assert [r["own"] for r in result["delete_permission_checks"]] == [True, True, None]
    assert [r["commands"] for r in result["delete_permission_checks"]] == [False, True, None]
    text = deletion_permission_text(data | result)
    assert all(t in text for t in ("一群", "二群", "三群", "无法删除用户报名命令", "暂时无法确认"))
    assert bot.get_chat_member.await_count == 3


@pytest.mark.parametrize(
    "member,kind,expected",
    [
        (SimpleNamespace(status="restricted", is_member=False), "supergroup", (False, False)),
        (
            SimpleNamespace(
                status="administrator", can_post_messages=True, can_delete_messages=False
            ),
            "channel",
            (True, False),
        ),
        (SimpleNamespace(status="administrator"), "group", (True, True)),
    ],
)
def test_deletion_rights_distinguish_chat_type_and_membership(member, kind, expected):
    assert _rights(member, kind) == expected


@pytest.mark.parametrize("registered", [True, False])
async def test_registration_failure_preserves_registered_fact_and_labels_action(registered):
    queries = SimpleNamespace(
        participant_campaign_detail=AsyncMock(
            return_value=SimpleNamespace(registration_completed=registered)
        )
    )
    text, keyboard = await registration_failure_feedback(
        queries,
        SimpleNamespace(public_ref="ref"),
        user_id=201,
        text="❌ 条件群未加入",
        keyboard=row_keyboard(
            [[InlineKeyboardButton(text="验证资格", callback_data="member:verify:ref")]]
        ),
    )
    assert ("已报名" if registered else "报名待完成") in text
    assert "❌ 条件群未加入" in text
    assert keyboard.inline_keyboard[0][0].text == (
        "🔄 核验并刷新" if registered else "🎟️ 核验并报名"
    )


@pytest.mark.parametrize("label", ["✅已关注", "❌ 未关注", "⏳ 待核验", "✅ 发言数"])
def test_state_buttons_do_not_gain_an_extra_function_icon(label):
    assert style_label(label) == label


async def _failed_helper(session_factory):
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        source = await _original_card(session, campaign, channel=True)
        await queue_channel_shortcut(session, source)
        await session.flush()
        helper = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_channel_shortcut")
        )
        helper.state = OutboxState.PRE_ACCEPT_FAILED
        return campaign.id, source.id, helper.id


async def test_retry_notices_preserves_main_card_and_unknown_result_and_business_key(
    session_factory,
):
    cid, source_id, helper_id = await _failed_helper(session_factory)
    async with session_factory() as session, session.begin():
        unknown = add_outbox(
            session,
            operation="send_start_card",
            aggregate_type="campaign",
            aggregate_id=str(cid),
            recipient_chat_id=-100123,
            business_key="unknown-start",
            payload={"source_outbox_id": str(source_id)},
        )
        unknown.state = OutboxState.UNKNOWN_HOLD
    count = await retry_failed_notices(
        partial(UnitOfWork, session_factory),
        cid,
        101,
        expected=ExpectedCampaign(state="running", revision=1),
    )
    assert count == 1
    async with session_factory() as session:
        helper = await session.get(TelegramOutbox, helper_id)
        assert helper.state == OutboxState.PENDING
        assert helper.business_key == f"channel_shortcut:{cid}:-100123"
        source = await session.get(TelegramOutbox, source_id)
        assert source.state == OutboxState.CONFIRMED and source.remote_message_id == 55
        assert (await session.get(TelegramOutbox, unknown.id)).state == OutboxState.UNKNOWN_HOLD
    with pytest.raises(UserFacingError, match="没有可安全重试"):
        await retry_failed_notices(partial(UnitOfWork, session_factory), cid, 101)


@pytest.mark.parametrize("blocked", ["admin", "revision", "closed", "cutoff", "source"])
async def test_retry_notice_rejects_unauthorized_or_stale_request(session_factory, blocked):
    cid, source_id, helper_id = await _failed_helper(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        if blocked == "closed":
            campaign.state = CampaignState.CANCELLED
        if blocked == "cutoff":
            campaign.cutoff_at = utcnow() - timedelta(seconds=1)
        if blocked == "source":
            (await session.get(TelegramOutbox, source_id)).recipient_chat_id = -999
    error = AuthorizationDenied if blocked == "admin" else UserFacingError
    with pytest.raises(error):
        await retry_failed_notices(
            partial(UnitOfWork, session_factory),
            cid,
            999 if blocked == "admin" else 101,
            expected=ExpectedCampaign(state="running", revision=2)
            if blocked == "revision"
            else None,
        )
    async with session_factory() as session:
        assert (await session.get(TelegramOutbox, helper_id)).state == OutboxState.PRE_ACCEPT_FAILED


async def test_helper_send_rechecks_original_source_authority(session_factory):
    _, source_id, helper_id = await _failed_helper(session_factory)
    async with session_factory() as session, session.begin():
        helper = await session.get(TelegramOutbox, helper_id)
        helper.state = OutboxState.CLAIMED
        helper.lease_owner = "preflight-worker"
        helper.lease_until = utcnow() + timedelta(minutes=2)
        (await session.get(TelegramOutbox, source_id)).state = OutboxState.UNKNOWN_HOLD
    assert await cancel_unauthorized_shortcut(session_factory, helper)
    async with session_factory() as session:
        row = await session.get(TelegramOutbox, helper_id)
        assert row.state == OutboxState.CANCELLED
        assert "original publication" in row.last_error


async def test_bad_channel_callback_is_explained_without_loading_or_sending():
    from tg_lottery.telegram.channel_shortcuts import handle_shortcut

    query = SimpleNamespace(data="cp:broken", answer=AsyncMock())
    assert await handle_shortcut(object(), query)
    assert "失效" in query.answer.await_args.args[0]


async def test_opening_message_settings_refreshes_and_displays_target_permissions():
    from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
    from tests.test_template_schedule import _complete_draft
    from tg_lottery.telegram.creator_settings import CreatorSettings

    data = _complete_draft() | {"panel_message_id": 123}
    sessions, renderer = ThresholdSessions(data), RecordingRenderer()
    bot = SimpleNamespace(
        id=901,
        get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup")),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
    )
    flow = CreatorSettings(
        sessions=sessions, renderer=renderer, profiles=object(), queries=object(), bot=bot
    )
    from tests.test_r14_queries import message

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=101), message=message("settings", user_id=101)
    )
    await flow._show_messages(query, None)
    assert "各群删除权限" in renderer.text
    assert "可以删除机器人短提示" in renderer.text
    assert sessions.data["delete_permission_checks"]
    assert bot.get_chat_member.await_count >= 1


async def test_failed_notice_management_action_requires_confirmation_and_expected_version():
    from tg_lottery.telegram.admin_confirmations import CONFIRMED_ACTIONS, confirmation_text
    from tg_lottery.telegram.admin_management_views import _shortcut_status_note
    from tg_lottery.telegram.admin_notice_recovery import notice_actions, retry_notices

    campaign = SimpleNamespace(
        state=CampaignState.RUNNING, cutoff_at=utcnow() + timedelta(hours=1), name="活动"
    )
    row = SimpleNamespace(label="频道个人交互入口", failed=1, pending=0, unknown=0)
    deliveries = SimpleNamespace(purposes=(row,))
    assert notice_actions(campaign, deliveries) == ("retry_notices",)
    assert "retry_notices" in CONFIRMED_ACTIONS
    assert "主卡" in confirmation_text(campaign, "retry_notices")
    assert "对应主卡已发布" in _shortcut_status_note(deliveries)
    flow = SimpleNamespace(
        _verify_admin=AsyncMock(),
        _campaigns=SimpleNamespace(retry_failed_notices=AsyncMock(return_value=1)),
    )
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=101), message=SimpleNamespace(answer=AsyncMock())
    )
    grant = SimpleNamespace(
        campaign_id="campaign", payload={"expected_state": "running", "expected_revision": 4}
    )
    await retry_notices(flow, query, grant)
    flow._verify_admin.assert_awaited_once_with("campaign", 101)
    expected = flow._campaigns.retry_failed_notices.await_args.kwargs["expected"]
    assert expected == ExpectedCampaign(state="running", revision=4)
    row.failed, row.unknown = 0, 1
    assert notice_actions(campaign, deliveries) == ()
