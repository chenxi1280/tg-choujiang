"""逐群累计有效发言: 与活动无关, 供参与门槛校验。"""

from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from tg_lottery.application.activity import MessageObservation
from tg_lottery.application.message_rules import (
    MIN_THRESHOLD_TEXT_LENGTH,
    normalize_text,
    rejection_reason,
)
from tg_lottery.application.message_threshold_notices import notify_threshold_crossing
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.request_context import current_update_id
from tg_lottery.infrastructure.db import MessageTotalEvent, UserGroupMessageTotal


class MessageStatsService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record(self, observation: MessageObservation) -> None:
        normalized = normalize_text(observation.text)
        if rejection_reason(observation, normalized, MIN_THRESHOLD_TEXT_LENGTH):
            return
        content_hash = sha256(normalized.encode()).hexdigest()
        async with self._uow_factory() as uow:
            await uow.session.execute(
                insert(UserGroupMessageTotal)
                .values(
                    user_id=observation.user_id,
                    chat_id=observation.chat_id,
                    total_count=0,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "chat_id"])
            )
            total = await uow.session.scalar(
                select(UserGroupMessageTotal)
                .where(
                    UserGroupMessageTotal.user_id == observation.user_id,
                    UserGroupMessageTotal.chat_id == observation.chat_id,
                )
                .with_for_update()
            )
            event_id = await uow.session.scalar(
                insert(MessageTotalEvent)
                .values(
                    chat_id=observation.chat_id,
                    message_id=observation.message_id,
                    user_id=observation.user_id,
                    event_at=observation.event_at,
                    content_hash=content_hash,
                    accepted=total.last_content_hash != content_hash,
                    source_update_id=current_update_id(),
                )
                .on_conflict_do_nothing(index_elements=["chat_id", "message_id"])
                .returning(MessageTotalEvent.id)
            )
            if event_id is None or total.last_content_hash == content_hash:
                return
            total.total_count += 1
            total.last_content_hash = content_hash
            total.last_message_at = observation.event_at
            await notify_threshold_crossing(uow.session, observation, count=total.total_count)

    async def totals(self, user_id: int, chat_ids: tuple[int, ...]) -> dict[int, int]:
        if not chat_ids:
            return {}
        async with self._uow_factory() as uow:
            rows = await uow.session.execute(
                select(UserGroupMessageTotal.chat_id, UserGroupMessageTotal.total_count).where(
                    UserGroupMessageTotal.user_id == user_id,
                    UserGroupMessageTotal.chat_id.in_(chat_ids),
                )
            )
            return {int(chat_id): int(count) for chat_id, count in rows.all()}
