from sqlalchemy import select

from tg_lottery.application.records import add_outbox
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import CampaignParticipationGroup, Group, TelegramOutbox


def ticket_status(participant: object) -> str:
    bonus = max(0, participant.ticket_total_cache - 1)
    if not participant.base_qualified:
        return f"当前累计加成 {bonus} 次, 暂存未生效; 基础门槛未完成, 有效抽奖次数 0。"
    if participant.qualification_state != QualificationState.ELIGIBLE:
        return f"当前累计加成 {bonus} 次; 其他参与资格未通过, 有效抽奖次数 0, 请重新验证资格。"
    return f"当前加成 {bonus} 次, 基础 1 次, 有效抽奖次数 {participant.ticket_total_cache}。"


async def queue_progress_notice(
    session: object, *, campaign: object, participant: object, chat_id: int, key: str, text: str
) -> None:
    if await session.scalar(select(TelegramOutbox.id).where(TelegramOutbox.business_key == key)):
        return
    add_outbox(
        session,
        operation="send_progress_notice",
        aggregate_type="participant",
        aggregate_id=str(participant.id),
        recipient_chat_id=chat_id,
        business_key=key,
        payload={
            "user_id": participant.user_id,
            "display_name": participant.display_name,
            "campaign_name": campaign.name,
            "campaign_ref": campaign.public_ref,
            "text": text,
            "ticket_status": ticket_status(participant),
        },
    )


async def queue_bonus_notice(
    session: object,
    *,
    campaign: object,
    participant: object,
    chat_id: int | None,
    key: str,
    label: str,
    delta: int,
) -> None:
    if chat_id is None:
        chat_id = await session.scalar(select(Group.chat_id).where(Group.id == campaign.group_id))
    if chat_id is None:
        raise ValueError("bonus notification group is missing")
    text = f"{label}已完成, 本次 +{delta} 次。"
    await queue_progress_notice(
        session,
        campaign=campaign,
        participant=participant,
        chat_id=chat_id,
        key=f"notice:{key}",
        text=text,
    )


async def invitation_notice_chat(session: object, invitation: object) -> int | None:
    if invitation.participation_group_id is None:
        return None
    return await session.scalar(
        select(CampaignParticipationGroup.chat_id_snapshot).where(
            CampaignParticipationGroup.id == invitation.participation_group_id
        )
    )
