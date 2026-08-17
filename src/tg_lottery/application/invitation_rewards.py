from datetime import datetime

from tg_lottery.domain.enums import TicketSource
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Campaign, Invitation, Participant, TicketLedger


async def add_invite_bonus(
    session: object,
    *,
    invitation: Invitation,
    campaign: Campaign,
    reward: int,
    business_key: str,
    occurred_at: datetime | None,
) -> None:
    participant = await session.get(Participant, invitation.inviter_participant_id)
    if participant is None:
        raise ValueError("inviter participant is missing")
    session.add(
        TicketLedger(
            campaign_id=campaign.id,
            participant_id=participant.id,
            delta=reward,
            source_type=TicketSource.INVITATION if reward > 0 else TicketSource.REVERSAL,
            source_ref=str(invitation.id),
            business_key=business_key,
            occurred_at=occurred_at,
            source_update_id=invitation.source_update_id,
        )
    )
    participant.ticket_total_cache += reward


def invite_bonus_target(rules: CampaignRules, invite_count: int) -> int:
    if rules.invite.mode == "ratio":
        divisor = rules.invite.valid_invites_per_ticket
        if divisor is None or rules.invite.max_bonus_tickets is None:
            raise ValueError("ratio invite reward rule is incomplete")
        return min(invite_count // divisor, rules.invite.max_bonus_tickets)
    target = 0
    for tier in rules.invite.reward_tiers:
        if invite_count < tier.invite_count:
            break
        target = tier.total_bonus
    if rules.invite.max_bonus_tickets is None:
        return target
    return min(target, rules.invite.max_bonus_tickets)
