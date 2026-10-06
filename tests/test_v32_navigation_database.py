from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram.types import Chat, Message, User

from tests.helpers import seed_campaign
from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackGrant, CallbackTokenService
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.infrastructure.db import AdminSession, Campaign, Group, GroupAdmin
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.creator_group_picker import CreatorGroupPicker
from tg_lottery.telegram.creator_group_selection import bind_initial_group
from tg_lottery.telegram.creator_renderer import CreatorRenderer, PanelTarget
from tg_lottery.telegram.draft_config import new_draft

TOTAL_CREATED_CAMPAIGNS = 22
CREATOR_ID = 101


async def test_creator_and_management_pages_do_not_stop_at_twenty_records(session_factory):
    async with session_factory() as session, session.begin():
        _, first, _ = await seed_campaign(session)
        session.add_all(
            _copy_campaign(first, number) for number in range(TOTAL_CREATED_CAMPAIGNS - 1)
        )
    queries = QueryService(session_factory)
    for managed in (False, True):
        first_page = await queries.creator_campaign_page(
            CREATOR_ID, options={"category": "active", "page": 0, "managed": managed}
        )
        assert first_page.total == TOTAL_CREATED_CAMPAIGNS
        ids = set()
        for number in range(first_page.page_count):
            page = await queries.creator_campaign_page(
                CREATOR_ID, options={"category": "active", "page": number, "managed": managed}
            )
            ids.update(item.id for item in page.rows)
        assert len(ids) == TOTAL_CREATED_CAMPAIGNS
        beyond = await queries.creator_campaign_page(
            CREATOR_ID,
            options={"category": "active", "page": first_page.page_count, "managed": managed},
        )
        assert beyond.page == first_page.page_count - 1


async def test_recovery_reads_committed_draft_source_when_session_has_no_campaign_id(
    session_factory,
):
    source_id = str(uuid4())
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign.source_draft_id = source_id
        campaign.source_draft_revision = 3
        campaign_id = campaign.id
        session.add(
            AdminSession(
                user_id=CREATOR_ID,
                state="draft:publish_confirm",
                data={"draft_id": source_id, "title": "原活动"},
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
    queries = QueryService(session_factory)
    state = await queries.creator_navigation_state(CREATOR_ID)
    assert state["published_campaign_id"] == str(campaign_id)
    recovered = await queries.campaign_for_draft(CREATOR_ID, source_id)
    assert recovered.id == campaign_id
    assert await queries.campaign_for_draft(CREATOR_ID + 1, source_id) is None


def _copy_campaign(first, number: int):
    return Campaign(
        group_id=first.group_id,
        name=f"分页活动 {number}",
        state=CampaignState.RUNNING,
        timezone=first.timezone,
        preheat_at=first.preheat_at,
        cutoff_at=first.cutoff_at,
        draw_at=first.draw_at,
        config_revision=1,
        rules=first.rules,
        presentation={},
        created_by=CREATOR_ID,
    )


async def test_confirmed_cancel_rejects_stale_state_inside_transaction(session_factory):
    from functools import partial

    import pytest

    from tg_lottery.application.campaigns import CampaignService
    from tg_lottery.domain.action_confirmation import ExpectedCampaign
    from tg_lottery.domain.errors import UserFacingError
    from tg_lottery.infrastructure.db.uow import UnitOfWork

    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign_id = campaign.id
    service = CampaignService(partial(UnitOfWork, session_factory))
    with pytest.raises(UserFacingError, match="确认未执行"):
        await service.change_state(
            campaign_id,
            actor_user_id=CREATOR_ID,
            target=CampaignState.CANCELLED,
            reason="验收",
            expected=ExpectedCampaign(str(CampaignState.PAUSED), 1),
        )
    async with session_factory() as session:
        assert (await session.get(Campaign, campaign_id)).state is CampaignState.RUNNING


async def test_migrated_target_preserves_gate_progress_and_current_join_route(session_factory):
    from functools import partial

    from tests.v30_helpers import FIRST, START, rules_for, seed_v3, speech
    from tg_lottery.application.groups import GroupService
    from tg_lottery.infrastructure.db.uow import UnitOfWork

    campaign_id, _ = await seed_v3(session_factory, rules_for("message", ("activity",)))
    await speech(session_factory, count=1)
    new_chat = -100999
    await GroupService(partial(UnitOfWork, session_factory)).migrate_group(FIRST, new_chat)
    await speech(session_factory, chat_id=new_chat, count=1, at=START, first_id=900)
    queries = QueryService(session_factory)
    target = next(
        item
        for item in await queries.qualification_targets(campaign_id)
        if item.kind == "participation_group"
    )
    assert target.chat_id == new_chat and target.rule_chat_id == FIRST
    group = await queries.group_by_chat_id(FIRST)
    assert group.chat_id == new_chat
    progress = await queries.group_bonus_progress(campaign_id, 201)
    assert progress.activities[0].chat_id == FIRST
    assert progress.activities[0].total_awarded_tickets == 1


async def test_management_candidates_require_all_cached_participation_group_roles(session_factory):
    from sqlalchemy import select

    from tests.v30_helpers import SECOND, rules_for, seed_v3
    from tg_lottery.infrastructure.db import Group, GroupAdmin

    campaign_id, _ = await seed_v3(session_factory, rules_for("message"))
    queries = QueryService(session_factory)
    options = {"category": "active", "page": 0, "managed": True}
    assert (await queries.creator_campaign_page(CREATOR_ID, options=options)).rows == ()
    async with session_factory() as session, session.begin():
        second = await session.scalar(select(Group).where(Group.chat_id == SECOND))
        session.add(GroupAdmin(group_id=second.id, user_id=CREATOR_ID, source="owner", active=True))
    page = await queries.creator_campaign_page(CREATOR_ID, options=options)
    assert tuple(campaign.id for campaign in page.rows) == (campaign_id,)
    assert (
        await queries.creator_campaign_page(CREATOR_ID, options=options | {"managed": False})
    ).rows[0].id == campaign_id


async def test_group_picker_page_and_page_selection_share_the_same_actual_slice(session_factory):
    async with session_factory() as session, session.begin():
        group, _, _ = await seed_campaign(session)
        for number in range(7):
            added = Group(chat_id=-100500 - number, title=f"A{number}", timezone="Asia/Shanghai")
            session.add(added)
            await session.flush()
            session.add(
                GroupAdmin(group_id=added.id, user_id=CREATOR_ID, source="owner", active=True)
            )
        data = bind_initial_group(new_draft(), group) | {"panel_message_id": 10}
    factory = partial(UnitOfWork, session_factory)
    sessions, tokens, queries = (
        AdminSessionService(factory),
        CallbackTokenService(factory),
        QueryService(session_factory),
    )
    await sessions.set(CREATOR_ID, "draft:idle", data)
    bot = SimpleNamespace(
        id=999,
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="creator")),
        get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup")),
        edit_message_text=AsyncMock(),
    )
    renderer = CreatorRenderer(bot=bot, tokens=tokens, queries=queries, sessions=sessions)
    await renderer.edit_groups(
        PanelTarget(CREATOR_ID, CREATOR_ID, 10),
        selected_chat_ids=tuple(data["participation_group_ids"]),
        page=99,
    )
    stored = await sessions.get(CREATOR_ID)
    assert stored.data["group_page"] == 1
    assert "页码已随当前群列表调整" in bot.edit_message_text.await_args.args[0]
    picker = CreatorGroupPicker(bot=bot, sessions=sessions, queries=queries, renderer=renderer)
    visible = await picker._visible_groups(CREATOR_ID, dict(stored.data))
    message = Message(
        message_id=10, date=datetime.now(UTC), chat=Chat(id=CREATOR_ID, type="private")
    )
    query = SimpleNamespace(
        from_user=User(id=CREATOR_ID, first_name="Owner", is_bot=False), message=message
    )
    await picker.select_page(query, CallbackGrant("draft_groups_select_page", None, {}))
    selected = (await sessions.get(CREATOR_ID)).data["participation_group_ids"]
    assert {group.chat_id for group in visible}.issubset(selected)
    assert len(selected) == len(set(selected))


async def test_draft_write_records_durable_input_source_for_feedback_retry(session_factory):
    from tg_lottery.application.request_context import bind_update_context

    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    with bind_update_context(999, 100):
        await sessions.set(
            CREATOR_ID, "draft:prize_count", new_draft() | {"pending_prize_name": "无价值奖品"}
        )
    row = await sessions.get(CREATOR_ID)
    assert row.data["mutation_source_update_id"] == 100
    assert row.state == "draft:prize_count"
    assert row.data["pending_prize_name"] == "无价值奖品"
    assert row.data["prizes"] == []


async def test_older_callback_retry_cannot_reactivate_a_field_after_newer_navigation(
    session_factory,
):
    import pytest

    from tg_lottery.application.request_context import bind_update_context
    from tg_lottery.domain.errors import UserFacingError

    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    with bind_update_context(999, 101):
        await sessions.set(CREATOR_ID, "draft:suspended", new_draft() | {"title": "最新标题"})
    with bind_update_context(999, 100), pytest.raises(UserFacingError, match="原请求未再次修改"):
        await sessions.set(CREATOR_ID, "draft:title", new_draft() | {"title": "旧标题"})
    row = await sessions.get(CREATOR_ID)
    assert row.state == "draft:suspended"
    assert row.data["title"] == "最新标题"
    assert row.data["mutation_source_update_id"] == 101
