from sqlalchemy import func, select

from tg_lottery.application.admission_progress import record_group_admission
from tg_lottery.application.progress_notifications import queue_progress_notice
from tg_lottery.domain.enums import InvitationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import CampaignParticipationGroup, Invitation, Participant


async def update_invite_admission(session: object, *, campaign: object, invitation: object):
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.admission is None or rules.admission.type != "invite":
        return
    target = await session.get(CampaignParticipationGroup, invitation.participation_group_id)
    if target is None:
        raise ValueError("invitation admission requires a participation group")
    rule = rules.admission.groups.get(str(target.chat_id_snapshot))
    if rule is None:
        return
    participant = await session.get(
        Participant, invitation.inviter_participant_id, with_for_update=True
    )
    if participant is None:
        raise ValueError("inviter participant is missing")
    count = await _valid_group_invites(
        session, campaign=campaign, participant=participant, target=target
    )
    if count < rule.required_valid_invites:
        return
    created = await record_group_admission(
        session,
        campaign=campaign,
        participant=participant,
        chat_id=target.chat_id_snapshot,
        event_at=invitation.due_at or invitation.joined_at,
        source_ref=f"invitation:{invitation.id}",
    )
    if created:
        await queue_progress_notice(
            session,
            campaign=campaign,
            participant=participant,
            chat_id=target.chat_id_snapshot,
            key=f"invite_admission:{participant.id}:{target.id}",
            text=f"本群邀请门槛已完成: 已确认 {count}/{rule.required_valid_invites} 名有效新成员。",
        )


async def _valid_group_invites(
    session: object, *, campaign: object, participant: object, target: object
) -> int:
    count = await session.scalar(
        select(func.count(Invitation.id)).where(
            Invitation.campaign_id == campaign.id,
            Invitation.inviter_participant_id == participant.id,
            Invitation.participation_group_id == target.id,
            Invitation.state == InvitationState.VALID,
        )
    )
    return int(count or 0)
