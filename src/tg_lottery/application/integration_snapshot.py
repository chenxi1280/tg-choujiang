"""Bounded read projection; it neither registers participants nor awards tickets."""

import hashlib
import json
import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import String, cast, func, or_, select

from tg_lottery.application.admission_qualification import behavior_qualified
from tg_lottery.application.integration_notifications import participant_notifications
from tg_lottery.application.records import utcnow
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignRequirement,
    DrawRun,
    DrawSnapshot,
    Participant,
    Prize,
    TelegramOutbox,
    Winner,
)
from tg_lottery.infrastructure.db.models_base_activity import BaseActivityEvent
from tg_lottery.infrastructure.db.models_engagement import ActivityEvent, TicketLedger


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[int] = Field(default_factory=list, max_length=100)
    user_digests: list[str] = Field(default_factory=list, max_length=100)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def bounded_scope(self):
        if self.date_to < self.date_from or (self.date_to - self.date_from).days > 30:
            raise ValueError("snapshot date range must be at most 31 days")
        if bool(self.user_ids) == bool(self.user_digests):
            raise ValueError("supply exactly one identity scope")
        if self.user_digests:
            _validate_digests(self.user_digests)
            return self
        if min(self.user_ids) <= 0 or len(set(self.user_ids)) != len(self.user_ids):
            raise ValueError("user IDs must be unique and positive")
        if max(self.user_ids) > 2**63 - 1:
            raise ValueError("user ID exceeds signed 64-bit range")
        return self


async def campaign_snapshot(
    session, public_ref, request, *, creator_ids, bot_id, campaign_refs=frozenset()
):
    campaign = await session.scalar(
        select(Campaign).where(
            Campaign.public_ref == public_ref,
            or_(Campaign.created_by.in_(creator_ids), Campaign.public_ref.in_(campaign_refs)),
        )
    )
    if campaign is None:
        return None
    participants = list(
        await session.scalars(
            select(Participant).where(
                Participant.campaign_id == campaign.id, _identity_filter(request)
            )
        )
    )
    ids = [p.id for p in participants]
    base = await _counts(session, BaseActivityEvent, campaign.id, ids=ids, request=request)
    bonus = await _counts(session, ActivityEvent, campaign.id, ids=ids, request=request)
    awards = await _awards(session, campaign.id, ids)
    wins = await _wins(session, campaign.id, ids)
    rows = {
        p.user_id: _participant(p, campaign, base=base, bonus=bonus, awards=awards, wins=wins)
        for p in participants
    }
    notices = await participant_notifications(session, campaign.id, ids)
    for participant in participants:
        rows[participant.user_id]["notification_delivery"] = notices.get(participant.id, [])
    return await _snapshot_document(session, campaign, request, bot_id=bot_id, rows=rows)


async def _snapshot_document(session, campaign, request, *, bot_id, rows):
    rules = CampaignRules.model_validate(campaign.rules).model_dump(mode="json")
    rules_hash = hashlib.sha256(
        json.dumps(rules, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": "lottery-integration-v1",
        "observed_at": utcnow().isoformat(),
        "bot_id": str(bot_id),
        "campaign_ref": campaign.public_ref,
        "campaign_id": str(campaign.id),
        "name": campaign.name,
        "state": campaign.state.value,
        "preheat_at": campaign.preheat_at.isoformat(),
        "cutoff_at": campaign.cutoff_at.isoformat(),
        "draw_at": campaign.draw_at.isoformat(),
        "rules_hash": rules_hash,
        "rules": rules,
        "group_identities": await _public_group_identities(session, campaign.id),
        "date_from": request.date_from.isoformat(),
        "date_to": request.date_to.isoformat(),
        "participants": _scoped_rows(rows, request),
        "group_result_notifications": await _group_notifications(session, campaign.id),
    }


async def _public_group_identities(session, campaign_id):
    published = await session.execute(
        select(
            CampaignParticipationGroup.chat_id_snapshot,
            CampaignParticipationGroup.username_snapshot,
        )
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            CampaignParticipationGroup.private.is_(False),
            CampaignParticipationGroup.publication_state == "confirmed",
        )
        .limit(101)
    )
    required = await session.execute(
        select(CampaignRequirement.chat_id_snapshot, CampaignRequirement.username)
        .where(
            CampaignRequirement.campaign_id == campaign_id,
            CampaignRequirement.private.is_(False),
            CampaignRequirement.chat_type.in_(("group", "supergroup")),
        )
        .limit(101)
    )
    rows = list(published) + list(required)
    if len(rows) > 100:
        raise ValueError("lottery_group_identity_scope_exceeds_limit")
    identities = {
        (str(chat_id), str(username).lstrip("@"))
        for chat_id, username in rows
        if username
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", str(username).lstrip("@"))
        and re.fullmatch(r"-\d+", str(chat_id))
    }
    return [{"chat_id": peer, "username": username} for peer, username in sorted(identities)]


async def _counts(session, model, campaign_id, *, ids, request):
    if not ids:
        return {}
    rows = await session.execute(
        select(model.participant_id, model.chat_id, model.local_date, func.count(model.id))
        .where(
            model.campaign_id == campaign_id,
            model.participant_id.in_(ids),
            model.accepted.is_(True),
            model.local_date >= request.date_from,
            model.local_date <= request.date_to,
        )
        .group_by(model.participant_id, model.chat_id, model.local_date)
    )
    result = {}
    for participant_id, chat_id, day, count in rows:
        result.setdefault(participant_id, []).append(
            {
                "chat_id": str(chat_id),
                "date": day.isoformat(),
                "accepted_messages": count,
            }
        )
    return result


async def _awards(session, campaign_id, ids):
    if not ids:
        return {}
    rows = await session.execute(
        select(TicketLedger.participant_id, TicketLedger.source_type, func.sum(TicketLedger.delta))
        .where(TicketLedger.campaign_id == campaign_id, TicketLedger.participant_id.in_(ids))
        .group_by(TicketLedger.participant_id, TicketLedger.source_type)
    )
    result = {}
    for participant_id, source_type, quantity in rows:
        # PostgreSQL returns Decimal for SUM(bigint); ticket deltas are integers.
        result.setdefault(participant_id, {})[source_type.value] = int(quantity)
    return result


async def _wins(session, campaign_id, ids):
    if not ids:
        return {}
    rows = await session.execute(
        select(Winner, Prize.name, TelegramOutbox.state, TelegramOutbox.remote_message_id)
        .join(Prize, Prize.id == Winner.prize_id)
        .join(Participant, Participant.id == Winner.participant_id)
        .outerjoin(
            TelegramOutbox,
            (TelegramOutbox.aggregate_type == "winner")
            & (TelegramOutbox.aggregate_id == cast(Winner.id, String))
            & (TelegramOutbox.operation == "send_winner_notice")
            & (TelegramOutbox.recipient_chat_id == Participant.user_id),
        )
        .where(Prize.campaign_id == campaign_id, Winner.participant_id.in_(ids))
    )
    result = {}
    for winner, name, notice_state, message_id in rows:
        result.setdefault(winner.participant_id, []).append(
            {
                "winner_id": str(winner.id),
                "prize_name": name,
                "claim_state": winner.claim_state.value,
                "private_notice": {
                    "state": notice_state.value if notice_state else "not_observed",
                    "remote_message_id": str(message_id) if message_id else None,
                },
            }
        )
    return result


def _participant(participant, campaign, *, base, bonus, awards, wins):
    return {
        "user_id": str(participant.user_id),
        "registered": True,
        "participant_id": str(participant.id),
        "qualification_state": participant.qualification_state.value,
        "behavior_qualified": behavior_qualified(participant, campaign),
        "counting_started_at": participant.counting_started_at.isoformat()
        if participant.counting_started_at
        else None,
        "ticket_total": participant.ticket_total_cache,
        "ticket_ledger_by_source": awards.get(participant.id, {}),
        "base_counts": base.get(participant.id, []),
        "bonus_counts": bonus.get(participant.id, []),
        "winners": wins.get(participant.id, []),
        "notification_delivery": "see_winner_private_notices_and_campaign_group_results",
    }


def _identity_filter(request):
    if request.user_ids:
        return Participant.user_id.in_(request.user_ids)
    digest = func.encode(
        func.sha256(func.convert_to(cast(Participant.user_id, String), "UTF8")), "hex"
    )
    return digest.in_(request.user_digests)


def _scoped_rows(rows, request):
    if request.user_ids:
        return [
            rows.get(uid, {"user_id": str(uid), "registered": False}) for uid in request.user_ids
        ]
    by_digest = {hashlib.sha256(str(uid).encode()).hexdigest(): row for uid, row in rows.items()}
    return [
        {**by_digest.get(digest, {"registered": False}), "user_digest": digest}
        for digest in request.user_digests
    ]


def _validate_digests(values):
    import re

    if len(set(values)) != len(values) or any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in values):
        raise ValueError("invalid identity digests")


async def _group_notifications(session, campaign_id):
    rows = list(
        await session.execute(
            select(
                TelegramOutbox.recipient_chat_id,
                TelegramOutbox.state,
                TelegramOutbox.remote_message_id,
            )
            .join(DrawRun, cast(DrawRun.id, String) == TelegramOutbox.aggregate_id)
            .join(DrawSnapshot, DrawSnapshot.id == DrawRun.snapshot_id)
            .where(
                DrawSnapshot.campaign_id == campaign_id,
                TelegramOutbox.aggregate_type == "draw_run",
                TelegramOutbox.operation == "send_draw_result",
            )
            .limit(1001)
        )
    )
    if len(rows) > 1000:
        raise ValueError("lottery_notification_scope_too_large")
    return [
        {
            "chat_id": str(chat_id),
            "state": state.value,
            "remote_message_id": str(message_id) if message_id else None,
        }
        for chat_id, state, message_id in rows
    ]
