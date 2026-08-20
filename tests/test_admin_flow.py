from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.groups import GroupService, TelegramAdmin
from tg_lottery.application.publication_queries import PublicationIssue
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.infrastructure.db import Group, Prize
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.admin_flow import (
    DRAW_CONFIRMATION_TEXT,
    AdminFlow,
    _designation_actions,
    _publication_actions,
    _publication_issue_state,
)
from tg_lottery.telegram.admin_winner_designations import _positions, _prompt


class FakeAdminBot:
    id = 9001

    def __init__(self, *, user_status: str, bot_status: str, can_invite: bool = True) -> None:
        self._user = SimpleNamespace(status=user_status)
        self._bot = SimpleNamespace(status=bot_status, can_invite_users=can_invite)

    async def get_chat_member(self, chat_id: int, user_id: int):
        return self._bot if user_id == self.id else self._user


def _flow(bot: FakeAdminBot) -> AdminFlow:
    return AdminFlow(
        bot=bot,
        creator=None,
        tokens=None,
        campaigns=None,
        drawing=None,
        winners=None,
        queries=None,
    )


async def test_current_user_and_bot_admin_status_is_required() -> None:
    await _flow(FakeAdminBot(user_status="creator", bot_status="administrator"))._verify_chat_admin(
        -1001, 101
    )
    with pytest.raises(AuthorizationDenied, match="管理员身份无效"):
        await _flow(
            FakeAdminBot(user_status="left", bot_status="administrator")
        )._verify_chat_admin(-1001, 101)
    with pytest.raises(AuthorizationDenied, match="管理员身份无效"):
        await _flow(FakeAdminBot(user_status="creator", bot_status="left"))._verify_chat_admin(
            -1001, 101
        )


async def test_publish_requires_bot_invite_permission() -> None:
    flow = _flow(FakeAdminBot(user_status="creator", bot_status="administrator", can_invite=False))
    with pytest.raises(AuthorizationDenied, match="管理邀请链接权限"):
        await flow._verify_chat_admin(-1001, 101, require_invites=True)


async def test_admin_promotion_and_bot_removal_update_cached_access(session_factory) -> None:
    async with session_factory() as session, session.begin():
        group, _, _ = await seed_campaign(session)
        chat_id = group.chat_id
    service = GroupService(partial(UnitOfWork, session_factory))
    queries = QueryService(session_factory)
    await service.sync_admin(
        chat_id=chat_id,
        admin=TelegramAdmin(user_id=202, source="telegram_admin"),
        active=True,
    )
    assert [group.chat_id for group in await queries.admin_groups(202)] == [chat_id]
    await service.deactivate_group(chat_id, observed_at=datetime.now(UTC))
    assert await queries.admin_groups(101) == []
    assert await queries.admin_groups(202) == []


async def test_every_synced_group_admin_can_create_for_the_group(session_factory) -> None:
    service = GroupService(partial(UnitOfWork, session_factory))
    await service.sync_group(
        chat_id=-100700,
        title="双管理员群",
        timezone="Asia/Shanghai",
        permissions={},
        admins=(
            TelegramAdmin(user_id=101, source="owner"),
            TelegramAdmin(user_id=202, source="telegram_admin"),
        ),
    )
    queries = QueryService(session_factory)

    assert [group.chat_id for group in await queries.admin_groups(101)] == [-100700]
    assert [group.chat_id for group in await queries.admin_groups(202)] == [-100700]


async def test_unregistered_group_migration_is_ignored(session_factory) -> None:
    service = GroupService(partial(UnitOfWork, session_factory))

    await service.migrate_group(-5001, -1005001)

    async with session_factory() as session:
        assert await session.scalar(select(Group)) is None


async def test_registered_group_migration_updates_chat_id(session_factory) -> None:
    async with session_factory() as session, session.begin():
        session.add(Group(chat_id=-5002, title="迁移群", timezone="Asia/Shanghai"))
    service = GroupService(partial(UnitOfWork, session_factory))

    await service.migrate_group(-5002, -1005002)

    async with session_factory() as session:
        group = await session.scalar(select(Group))
        assert group.chat_id == -1005002
        assert group.migrated_from_chat_id == -5002


def test_draw_confirmation_uses_business_language_only() -> None:
    assert DRAW_CONFIRMATION_TEXT == "开奖结果已生成, 群公告和中奖通知正在发送。"
    assert "数据库" not in DRAW_CONFIRMATION_TEXT
    assert "独立送达" not in DRAW_CONFIRMATION_TEXT


def test_designation_action_is_available_only_before_drawing() -> None:
    assert _designation_actions(CampaignState.RUNNING) == ("designate_winner",)
    assert _designation_actions(CampaignState.READY_TO_DRAW) == ("designate_winner",)
    assert _designation_actions(CampaignState.DRAWING) == ()
    assert _designation_actions(CampaignState.DRAWN) == ()


async def test_designation_prompt_lists_available_prize_slots(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, _, _ = await seed_campaign(session)
        prize = await session.scalar(select(Prize))
        assert prize is not None
        text = _prompt((prize,), ())

    assert f"第{prize.position}顺位 {prize.name}" in text
    assert f"名额 1-{prize.winner_count}" in text
    assert _positions("1", "2") == (1, 2)


def test_publication_buttons_never_retry_unknown_delivery() -> None:
    unknown = PublicationIssue(-1001, "未知群", OutboxState.UNKNOWN_HOLD, "timeout")
    failed = PublicationIssue(-1002, "失败群", OutboxState.PRE_ACCEPT_FAILED, "rejected")

    assert _publication_actions(CampaignState.PUBLISHING, (unknown,)) == (
        "refresh_publication",
        "publication_issues",
    )
    assert _publication_actions(CampaignState.PUBLISHING, (unknown, failed)) == (
        "refresh_publication",
        "publication_issues",
        "retry_publication",
    )
    assert "不会自动重试" in _publication_issue_state(OutboxState.UNKNOWN_HOLD)
