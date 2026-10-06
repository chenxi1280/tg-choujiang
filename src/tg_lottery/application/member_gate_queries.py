from sqlalchemy import select

from tg_lottery.application.admission_views import _completion_records, _group_lines
from tg_lottery.application.base_activity_views import _progress_completed, _progress_observed_at
from tg_lottery.application.campaign_references import resolve_campaign_reference
from tg_lottery.application.records import utcnow
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Participant


async def load_gate_group_lines(factory, user_id, reference):
    async with factory() as session:
        campaign = await resolve_campaign_reference(session, reference)
        if campaign is None:
            return None
        participant = await session.scalar(
            select(Participant).where(
                Participant.campaign_id == campaign.id, Participant.user_id == user_id
            )
        )
        if participant is None:
            return None
        rules = CampaignRules.model_validate(campaign.rules)
        if rules.admission is None:
            return {}
        now = utcnow()
        observed = await _progress_observed_at(session, campaign, now)
        if observed is None:
            return {
                key: "历史记录截止时间未确认, 需要核查取消或开奖记录。"
                for key in rules.admission.groups
            }
        records = await _completion_records(session, participant.id)
        lines = await _group_lines(
            session,
            campaign=campaign,
            participant=participant,
            rules=rules,
            observed_at=observed,
            records=records,
            completed=_progress_completed(campaign, now),
        )
        return dict(zip(rules.admission.groups, lines, strict=True))
