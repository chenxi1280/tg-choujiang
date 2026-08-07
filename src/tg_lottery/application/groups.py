from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import Group, GroupAdmin


@dataclass(frozen=True)
class TelegramAdmin:
    user_id: int
    source: str


class GroupService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def sync_group(
        self,
        *,
        chat_id: int,
        title: str,
        timezone: str,
        permissions: dict[str, object],
        admins: tuple[TelegramAdmin, ...],
        observed_at: datetime | None = None,
    ) -> Group:
        async with self._uow_factory() as uow:
            group = await uow.session.scalar(select(Group).where(Group.chat_id == chat_id))
            group = group or Group(chat_id=chat_id, title=title, timezone=timezone)
            group.title = title
            group.permissions = permissions
            group.permission_checked_at = observed_at or utcnow()
            uow.session.add(group)
            await uow.session.flush()
            await self._replace_admins(uow.session, group, admins)
            return group

    async def migrate_group(self, old_chat_id: int, new_chat_id: int) -> None:
        async with self._uow_factory() as uow:
            group = await uow.session.scalar(
                select(Group).where(Group.chat_id == old_chat_id).with_for_update()
            )
            if group is None:
                raise ValueError("group to migrate was not registered")
            group.migrated_from_chat_id = old_chat_id
            group.chat_id = new_chat_id

    async def sync_admin(
        self,
        *,
        chat_id: int,
        admin: TelegramAdmin,
        active: bool,
    ) -> None:
        async with self._uow_factory() as uow:
            group = await uow.session.scalar(select(Group).where(Group.chat_id == chat_id))
            if group is None:
                return
            row = await uow.session.scalar(
                select(GroupAdmin).where(
                    GroupAdmin.group_id == group.id,
                    GroupAdmin.user_id == admin.user_id,
                )
            )
            if row is None:
                if active:
                    uow.session.add(
                        GroupAdmin(
                            group_id=group.id,
                            user_id=admin.user_id,
                            source=admin.source,
                            active=True,
                        )
                    )
                return
            row.source = admin.source
            row.active = active

    async def deactivate_group(self, chat_id: int, observed_at: datetime) -> None:
        async with self._uow_factory() as uow:
            group = await uow.session.scalar(select(Group).where(Group.chat_id == chat_id))
            if group is None:
                return
            group.permission_checked_at = observed_at
            rows = (
                await uow.session.scalars(select(GroupAdmin).where(GroupAdmin.group_id == group.id))
            ).all()
            for row in rows:
                row.active = False

    async def _replace_admins(
        self,
        session: object,
        group: Group,
        admins: tuple[TelegramAdmin, ...],
    ) -> None:
        rows = (
            await session.scalars(select(GroupAdmin).where(GroupAdmin.group_id == group.id))
        ).all()
        by_user = {row.user_id: row for row in rows}
        active_users = {admin.user_id for admin in admins}
        for row in rows:
            row.active = row.user_id in active_users
        for admin in admins:
            row = by_user.get(admin.user_id)
            if row:
                row.source = admin.source
                row.active = True
            else:
                session.add(
                    GroupAdmin(
                        group_id=group.id,
                        user_id=admin.user_id,
                        source=admin.source,
                        active=True,
                    )
                )
