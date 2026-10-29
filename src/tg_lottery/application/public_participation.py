from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, or_, select

from tg_lottery.application.admission_qualification import behavior_clause
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState, TicketSource
from tg_lottery.infrastructure.db import Participant, TicketLedger


def successful_registration():
    return or_(
        Participant.counting_started_at.is_not(None),
        exists(
            select(TicketLedger.id).where(
                TicketLedger.participant_id == Participant.id,
                TicketLedger.source_type == TicketSource.REGISTRATION,
                TicketLedger.delta > 0,
            )
        ),
    )


async def public_participation(session, campaign_id):
    registered = successful_registration()
    total, eligible = (
        await session.execute(
            select(
                func.count(Participant.id).filter(registered),
                func.count(Participant.id).filter(
                    registered,
                    Participant.qualification_state == QualificationState.ELIGIBLE,
                    behavior_clause(),
                ),
            ).where(Participant.campaign_id == campaign_id)
        )
    ).one()
    moment = utcnow().astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M:%S")
    return (
        f"👥 参与情况: {eligible}人已记录达标 / {total}人报名 · 截至{moment}"
        " (北京时间, 以开奖复核为准)"
    )


class LotteryReadQueries:
    async def public_participation(self, campaign_id):
        async with self._session_factory() as session:
            return await public_participation(session, campaign_id)
