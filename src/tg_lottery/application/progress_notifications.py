from sqlalchemy import select

from tg_lottery.application.activity_bonus_reminders import activity_bonus_progress_text
from tg_lottery.application.activity_targets import message_rule_chat_id
from tg_lottery.application.admission_qualification import behavior_qualified
from tg_lottery.application.participation_progress import brief_task_progress
from tg_lottery.application.progress_group_links import ProgressGroupLabels, progress_group_links
from tg_lottery.application.records import add_outbox
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import CampaignParticipationGroup, Group, TelegramOutbox


def ticket_status(participant: object, campaign: object | None = None) -> str:
    bonus = max(0, participant.ticket_total_cache - 1)
    qualified = (
        behavior_qualified(participant, campaign) if campaign else participant.base_qualified
    )
    if not qualified:
        label = "行为门槛" if campaign and campaign.rules.get("admission") else "基础门槛"
        return f"🎟️ 有效抽奖次数 0 · 加成 {bonus} 次暂存({label}未完成)"
    if participant.qualification_state != QualificationState.ELIGIBLE:
        return f"🎟️ 有效抽奖次数 0 · 加成 {bonus} 次 · ⚠️ 请重新验证资格"
    return f"🎟️ 有效抽奖次数 {participant.ticket_total_cache} · 加成 {bonus} 次 + 基础 1 次"


async def queue_progress_notice(
    session: object, *, campaign: object, participant: object, chat_id: int, key: str, text: str
) -> None:
    if campaign.rules.get("participation_rules_version") == "v3.0":
        chat_id = await message_rule_chat_id(session, campaign.id, chat_id)
        batch_key = (campaign.id, participant.id, chat_id)
        batch = dict(session.info.get("progress_notices") or {})
        events = dict(batch.get(batch_key) or {})
        batch[batch_key] = events | {key: text}
        session.info["progress_notices"] = batch
        return
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
            "ticket_status": ticket_status(participant, campaign),
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
    text = f"🎉 {label} +{delta} 次"
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
        select(Group.chat_id)
        .join(CampaignParticipationGroup, CampaignParticipationGroup.group_id == Group.id)
        .where(CampaignParticipationGroup.id == invitation.participation_group_id)
    )


async def flush_progress_notices(
    session: object, *, campaign: object, participant: object, source_ref: str, event_at: object
) -> None:
    batch = dict(session.info.get("progress_notices") or {})
    matching = {
        key: events for key, events in batch.items() if key[:2] == (campaign.id, participant.id)
    }
    session.info["progress_notices"] = {
        key: events for key, events in batch.items() if key not in matching
    }
    if not matching:
        return
    detail, spans = await _linked_progress_detail(session, campaign, participant, event_at=event_at)
    for (_, _, chat_id), events in matching.items():
        business_key = f"progress:{participant.id}:{chat_id}:{source_ref}"
        if await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key == business_key)
        ):
            continue
        recipient = await _current_chat(session, campaign.id, chat_id)
        event_text = "\n".join(events.values()) + "\n\n"
        add_outbox(
            session,
            operation="send_progress_notice",
            aggregate_type="participant",
            aggregate_id=str(participant.id),
            recipient_chat_id=recipient,
            business_key=business_key,
            payload={
                "user_id": participant.user_id,
                "display_name": participant.display_name,
                "campaign_name": campaign.name,
                "campaign_ref": campaign.public_ref,
                "text": event_text + detail,
                "group_links": [
                    span | {"offset": span["offset"] + len(event_text)} for span in spans
                ],
                "event_refs": list(events),
                "occurred_at": event_at.isoformat(),
                "ticket_status": "完成时: " + ticket_status(participant, campaign),
            },
        )


async def _linked_progress_detail(session, campaign, participant, *, event_at):
    from tg_lottery.application.base_activity_views import base_progress_text

    labels = ProgressGroupLabels(await progress_group_links(session, campaign))
    progress = await base_progress_text(
        session, campaign, participant, now=event_at, group_label=labels.label, compact=True
    )
    bonus = await activity_bonus_progress_text(
        session, campaign, participant, event_at=event_at, group_label=labels.label, compact=True
    )
    full_detail, spans = labels.resolve("\n\n".join(text for text in (progress, bonus) if text))
    detail = brief_task_progress(full_detail)
    spans = [
        span for span in spans if detail.startswith(full_detail[: span["offset"] + span["length"]])
    ]
    return detail, spans


async def _current_chat(session, campaign_id, chat_id):
    current = await session.scalar(
        select(Group.chat_id)
        .join(CampaignParticipationGroup, CampaignParticipationGroup.group_id == Group.id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            CampaignParticipationGroup.chat_id_snapshot == chat_id,
        )
    )
    return current if current is not None else chat_id
