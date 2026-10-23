from sqlalchemy import BigInteger, cast, func, select

from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import BotRelationship, Campaign, Participant, QualificationCheck


class BotRelationshipService:
    def __init__(self, uow_factory, *, bot_id):
        self._uow_factory = uow_factory
        self.bot_id = bot_id

    async def record(self, user_id, observed_at, *, started=False, blocked=False):
        update_id = current_update_id()
        async with self._uow_factory() as uow:
            await uow.session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"bot-relation:{self.bot_id}:{user_id}", 0)
                    )
                )
            )
            row = await uow.session.get(
                BotRelationship, (self.bot_id, user_id), with_for_update=True
            )
            if row is None:
                row = BotRelationship(bot_id=self.bot_id, user_id=user_id, conflicted=False)
                uow.session.add(row)
            if started and row.started_at is None:
                row.started_at, row.start_update_id = observed_at, update_id
            if _apply_observation(row, observed_at, update_id=update_id, blocked=blocked):
                await _project_blocked_relationship(uow.session, row)

    async def observation(self, user_id):
        async with self._uow_factory() as uow:
            return await relationship_observation(uow.session, self.bot_id, user_id=user_id)


def _apply_observation(row, observed_at, *, update_id, blocked):
    if row.observed_at is not None and observed_at < row.observed_at:
        return False
    if row.observed_at == observed_at:
        before = row.conflicted
        row.conflicted = row.conflicted or blocked != row.blocked
        return row.conflicted != before
    row.observed_at, row.observed_update_id = observed_at, update_id
    row.blocked, row.conflicted = blocked, False
    return True


async def _project_blocked_relationship(session, row):
    if not row.blocked and not row.conflicted:
        return
    participants = await session.scalars(
        select(Participant)
        .join(Campaign, Campaign.id == Participant.campaign_id)
        .where(
            Participant.user_id == row.user_id,
            Campaign.state.in_(("scheduled", "running", "paused", "settling")),
            cast(Campaign.rules["platform_policy"]["bot_id"].as_string(), BigInteger) == row.bot_id,
        )
    )
    for participant in participants:
        state = QualificationState.UNKNOWN if row.conflicted else QualificationState.INELIGIBLE
        participant.qualification_state = state
        session.add(
            QualificationCheck(
                participant_id=participant.id,
                phase="bot_relationship_event",
                state=state,
                observed_at=row.observed_at,
                source_update_id=row.observed_update_id,
                observations={"chats": [], "bot_relation": {"state": state.value}},
                observed_status="bot_relationship_unknown" if row.conflicted else "bot_blocked",
            )
        )


async def record_blocked_delivery(session, *, bot_id, user_id, observed_at):
    await session.execute(
        select(
            func.pg_advisory_xact_lock(func.hashtextextended(f"bot-relation:{bot_id}:{user_id}", 0))
        )
    )
    row = await session.get(BotRelationship, (bot_id, user_id), with_for_update=True)
    if row is None:
        row = BotRelationship(bot_id=bot_id, user_id=user_id, conflicted=False)
        session.add(row)
    if _apply_observation(row, observed_at, update_id=None, blocked=True):
        await _project_blocked_relationship(session, row)


async def relationship_observation(session, bot_id, *, user_id, lock=False):
    row = await session.get(BotRelationship, (bot_id, user_id), with_for_update=lock)
    if row is None:
        from tg_lottery.application.bot_relationship_history import recover_relationship

        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"bot-relation:{bot_id}:{user_id}", 0)
                )
            )
        )
        row = await session.get(BotRelationship, (bot_id, user_id), populate_existing=True)
        row = row or await recover_relationship(session, bot_id, user_id=user_id)
    state, status = _relationship_state(row)
    return {
        "state": state.value,
        "observed_status": status,
        "bot_id": bot_id,
        "started_at": row.started_at.isoformat() if row and row.started_at else None,
        "observed_at": row.observed_at.isoformat() if row and row.observed_at else None,
        "update_id": row.observed_update_id if row else None,
    }


def _relationship_state(row):
    if row is None or row.started_at is None:
        state, status = QualificationState.INELIGIBLE, "bot_not_started"
    elif row.conflicted or row.blocked is None:
        state, status = QualificationState.UNKNOWN, "bot_relationship_unknown"
    elif row.blocked:
        state, status = QualificationState.INELIGIBLE, "bot_blocked"
    else:
        state, status = QualificationState.ELIGIBLE, "bot_started"
    return state, status
