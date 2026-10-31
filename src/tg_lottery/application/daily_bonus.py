"""Derive daily bonus eligibility from the existing message and ticket facts."""

from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, tuple_

from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    ActivityEvent,
    BaseActivityEvent,
    CampaignParticipationGroup,
    DailyProgress,
    Group,
)


@dataclass(frozen=True)
class DailyBonusGroup:
    chat_id: int
    messages: int
    required: int
    gate_ready: bool

    @property
    def ready(self):
        return self.gate_ready and self.messages >= self.required


@dataclass(frozen=True)
class DailyBonusProgress:
    first_day: bool
    awarded: int
    groups: tuple[DailyBonusGroup, ...]
    phase: str
    total_awarded: int
    starts_on: object

    @property
    def ready(self):
        return (
            self.phase == "running"
            and not self.first_day
            and bool(self.groups)
            and all(g.ready for g in self.groups)
        )


def _event_order(model):
    return (model.event_at, func.coalesce(model.source_update_id, -1), model.message_id)


async def daily_bonus_progress(session, campaign, participant, *, observed_at):
    rules = CampaignRules.model_validate(campaign.rules)
    day = observed_at.astimezone(ZoneInfo(campaign.timezone)).date()
    targets = await session.execute(
        select(CampaignParticipationGroup, Group.chat_id)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(CampaignParticipationGroup.campaign_id == campaign.id)
    )
    groups = []
    for target, live_chat in targets:
        rule = rules.activity_rule_for(target.chat_id_snapshot)
        if rule is not None:
            groups.append(
                await _group_progress(
                    session,
                    campaign,
                    participant,
                    rules=rules,
                    target=target,
                    live_chat=live_chat,
                    observed_at=observed_at,
                    day=day,
                    rule=rule,
                )
            )
    awarded, total = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(DailyProgress.tickets_awarded).filter(DailyProgress.local_date == day),
                    0,
                ),
                func.coalesce(func.sum(DailyProgress.tickets_awarded), 0),
            ).where(
                DailyProgress.participant_id == participant.id,
            )
        )
    ).one()
    first = campaign.preheat_at.astimezone(ZoneInfo(campaign.timezone)).date()
    return DailyBonusProgress(
        day == first,
        int(awarded),
        tuple(groups),
        _phase(campaign, observed_at),
        int(total),
        first + timedelta(days=1),
    )


async def _gate_boundary(session, campaign, participant, *, target, gate, day, observed_at):
    statement = select(BaseActivityEvent).where(
        BaseActivityEvent.participant_id == participant.id,
        BaseActivityEvent.participation_group_id == target.id,
        BaseActivityEvent.accepted.is_(True),
        BaseActivityEvent.event_at >= campaign.preheat_at,
        BaseActivityEvent.event_at <= min(observed_at, campaign.cutoff_at),
    )
    if gate.count_mode == "daily":
        statement = statement.where(BaseActivityEvent.local_date == day)
    return await session.scalar(
        statement.order_by(*_event_order(BaseActivityEvent))
        .offset(gate.daily_messages - 1)
        .limit(1)
    )


async def _group_progress(
    session, campaign, participant, *, rules, target, live_chat, observed_at, day, rule
):
    gate = rules.message_gate_rules.get(str(target.chat_id_snapshot))
    boundary = None
    if gate is not None:
        boundary = await _gate_boundary(
            session,
            campaign,
            participant,
            target=target,
            gate=gate,
            day=day,
            observed_at=observed_at,
        )
    statement = select(func.count(ActivityEvent.id)).where(
        ActivityEvent.participant_id == participant.id,
        ActivityEvent.chat_id.in_({live_chat, target.chat_id_snapshot}),
        ActivityEvent.local_date == day,
        ActivityEvent.accepted.is_(True),
        ActivityEvent.event_at >= campaign.preheat_at,
        ActivityEvent.event_at <= min(observed_at, campaign.cutoff_at),
    )
    if gate is not None and not rules.activity_bonus_double_count:
        if boundary is None:
            return DailyBonusGroup(target.chat_id_snapshot, 0, rule.messages_per_cycle, False)
        statement = statement.where(
            tuple_(*_event_order(ActivityEvent))
            > tuple_(boundary.event_at, boundary.source_update_id or -1, boundary.message_id)
        )
    messages = int(await session.scalar(statement))
    return DailyBonusGroup(
        target.chat_id_snapshot,
        messages,
        rule.messages_per_cycle,
        gate is None or boundary is not None,
    )


def _phase(campaign, observed_at):
    if observed_at > campaign.cutoff_at or campaign.state in {
        CampaignState.READY_TO_DRAW,
        CampaignState.DRAWING,
        CampaignState.DRAWN,
        CampaignState.CANCELLED,
        CampaignState.ARCHIVED,
    }:
        return "closed"
    if campaign.state == CampaignState.PAUSED:
        return "paused"
    if observed_at < campaign.preheat_at or campaign.state == CampaignState.SCHEDULED:
        return "scheduled"
    return "running"
