"""Bounded delivery evidence for selected participants, without message content."""

from sqlalchemy import String, cast, select

from tg_lottery.infrastructure.db import Participant, TelegramOutbox


async def participant_notifications(session, campaign_id, participant_ids):
    if not participant_ids:
        return {}
    rows = list(
        await session.execute(
            select(Participant.id, TelegramOutbox)
            .join(TelegramOutbox, TelegramOutbox.aggregate_id == cast(Participant.id, String))
            .where(
                Participant.campaign_id == campaign_id,
                Participant.id.in_(participant_ids),
                TelegramOutbox.aggregate_type == "participant",
                TelegramOutbox.operation == "send_progress_notice",
            )
            .order_by(TelegramOutbox.run_at.desc())
            .limit(5001)
        )
    )
    if len(rows) > 5000:
        raise ValueError("lottery_participant_notice_scope_too_large")
    result = {}
    for participant_id, notice in rows:
        result.setdefault(participant_id, []).append(
            {
                "kind": notice.business_key.split(":", 1)[0],
                "chat_id": str(notice.recipient_chat_id),
                "state": notice.state.value,
                "remote_message_id": str(notice.remote_message_id)
                if notice.remote_message_id
                else None,
            }
        )
    return result
