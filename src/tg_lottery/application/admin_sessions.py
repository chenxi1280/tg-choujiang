from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import AdminSession

_SESSION_TTL_HOURS = 24


@dataclass(frozen=True)
class DraftPanelContext:
    draft_id: str
    revision: int
    message_id: int

    def callback_payload(self) -> dict[str, object]:
        return {
            "draft_id": self.draft_id,
            "draft_revision": self.revision,
            "panel_message_id": self.message_id,
        }


class AdminSessionService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def set(self, user_id: int, state: str, data: dict[str, object]) -> None:
        async with self._uow_factory() as uow:
            row = await uow.session.get(AdminSession, user_id, with_for_update=True)
            if row is None:
                row = AdminSession(user_id=user_id, state=state, data={})
                uow.session.add(row)
            row.state = state
            row.data = data
            row.expires_at = utcnow() + timedelta(hours=_SESSION_TTL_HOURS)

    async def get(self, user_id: int) -> AdminSession | None:
        async with self._uow_factory() as uow:
            row = await uow.session.get(AdminSession, user_id, with_for_update=True)
            if row is None:
                return None
            if row.expires_at <= utcnow():
                await uow.session.delete(row)
                return None
            uow.session.expunge(row)
            return row

    async def clear(self, user_id: int) -> None:
        async with self._uow_factory() as uow:
            await uow.session.execute(delete(AdminSession).where(AdminSession.user_id == user_id))

    async def update(self, user_id: int, state: str, changes: dict[str, object]) -> None:
        current = await self.get(user_id)
        if current is None:
            raise ValueError("admin session expired")
        await self.set(user_id, state, {**current.data, **changes})

    async def issue_panel_context(self, user_id: int, message_id: int) -> DraftPanelContext:
        async with self._uow_factory() as uow:
            row = await uow.session.get(AdminSession, user_id, with_for_update=True)
            if row is None or row.expires_at <= utcnow() or not row.state.startswith("draft:"):
                raise ValueError("admin draft session expired")
            draft_id = str(row.data.get("draft_id", ""))
            if not draft_id:
                raise ValueError("admin draft identity is missing")
            revision = int(row.data.get("draft_revision", 0)) + 1
            row.data = {
                **row.data,
                "draft_revision": revision,
                "panel_message_id": message_id,
            }
            row.expires_at = utcnow() + timedelta(hours=_SESSION_TTL_HOURS)
            return DraftPanelContext(draft_id, revision, message_id)
