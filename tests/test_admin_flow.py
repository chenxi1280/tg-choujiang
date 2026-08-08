from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tg_lottery.application.groups import GroupService, TelegramAdmin
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.infrastructure.db import Group
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.admin_flow import AdminFlow


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
        bot_username="test_bot",
        sessions=None,
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
