from typing import TYPE_CHECKING

from tg_lottery.application.campaign_notifications import add_campaign_card_state_fanout
from tg_lottery.application.campaign_targets import participation_chat_ids
from tg_lottery.application.records import add_outbox
from tg_lottery.infrastructure.db import Campaign, DrawRun
from tg_lottery.presentation.draw_messages import draw_result_pages

if TYPE_CHECKING:
    from tg_lottery.application.drawing import Award, DrawOutcome


async def queue_draw_notifications(
    session: object, *, campaign: Campaign, draw_run: DrawRun, outcome: "DrawOutcome"
) -> None:
    presentation = campaign.presentation or {}
    winners = [_winner_payload(item) for item in outcome.awards]
    await add_campaign_card_state_fanout(
        session, campaign, state="drawn", winners=winners, completed_at=draw_run.completed_at
    )
    payload = {
        "campaign_name": campaign.name,
        "campaign_ref": campaign.public_ref,
        "winners": winners,
        "undistributed": [
            {
                "prize_position": item.prize.position,
                "prize_name": item.prize.name,
                "count": item.count,
            }
            for item in outcome.undistributed
        ],
        "extended_tips": bool(presentation.get("message_tips", True)),
        "creator": _creator_payload(campaign),
    }
    chat_ids = await participation_chat_ids(session, campaign.id, confirmed_only=True)
    for chat_id in chat_ids:
        _queue_group_result(session, draw_run=draw_run, chat_id=chat_id, payload=payload)
    _queue_winner_notices(session, campaign=campaign, outcome=outcome)
    add_outbox(
        session,
        operation="send_creator_draw_notice",
        aggregate_type="draw_run",
        aggregate_id=str(draw_run.id),
        recipient_chat_id=campaign.created_by,
        business_key=f"creator_draw_notice:{draw_run.id}:{campaign.created_by}",
        payload={
            "campaign_name": campaign.name,
            "campaign_ref": campaign.public_ref,
            "winner_count": len({item.participant.user_id for item in outcome.awards}),
            "awarded_slots": len(outcome.awards),
            "undistributed_count": sum(item.count for item in outcome.undistributed),
        },
    )


def _creator_payload(campaign: Campaign) -> dict[str, object]:
    presentation = campaign.presentation or {}
    is_issuer = presentation.get("issuer_user_id") == campaign.created_by
    return {
        "user_id": campaign.created_by,
        "display_name": (presentation.get("issuer_name") if is_issuer else None) or "活动创建者",
        "username": presentation.get("issuer_username_at_publish") if is_issuer else None,
    }


def _queue_group_result(
    session: object, *, draw_run: DrawRun, chat_id: int, payload: dict[str, object]
) -> None:
    for index, page in enumerate(draw_result_pages(payload)):
        suffix = f":page:{index + 1}" if index else ""
        add_outbox(
            session,
            operation="send_draw_result",
            aggregate_type="draw_run",
            aggregate_id=str(draw_run.id),
            recipient_chat_id=chat_id,
            business_key=f"draw_result_group:{draw_run.id}:{chat_id}{suffix}",
            payload=page,
        )


def _queue_winner_notices(session: object, *, campaign: Campaign, outcome: "DrawOutcome") -> None:
    presentation = campaign.presentation or {}
    for item in outcome.awards:
        add_outbox(
            session,
            operation="send_winner_notice",
            aggregate_type="winner",
            aggregate_id=str(item.winner.id),
            recipient_chat_id=item.participant.user_id,
            business_key=f"winner_notice:{item.winner.id}:{item.participant.user_id}",
            payload={
                "campaign_name": campaign.name,
                "campaign_ref": campaign.public_ref,
                "prize_name": item.prize.name,
                "prize_slot_no": item.prize_slot_no,
                "claim_instructions": item.prize.claim_instructions,
                "extended_tips": bool(presentation.get("message_tips", True)),
                "issuer_name": presentation.get("issuer_name") or "活动创建者",
                "issuer_user_id": presentation.get("issuer_user_id") or campaign.created_by,
                "issuer_username": presentation.get("issuer_username_at_publish"),
            },
        )


def _winner_payload(item: "Award") -> dict[str, object]:
    return {
        "user_id": item.participant.user_id,
        "display_name": item.participant.display_name,
        "username": item.participant.username,
        "prize_position": item.prize.position,
        "prize_name": item.prize.name,
        "prize_slot_no": item.prize_slot_no,
    }
