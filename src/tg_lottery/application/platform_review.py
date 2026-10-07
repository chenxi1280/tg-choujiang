"""Finite, campaign-specific qualification evidence required before freezing a snapshot."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select

from tg_lottery.application.admission_qualification import behavior_qualified
from tg_lottery.application.bot_relationship_history import projected_start_exists
from tg_lottery.application.bot_relationships import relationship_observation
from tg_lottery.application.campaign_targets import qualification_targets
from tg_lottery.application.records import add_job
from tg_lottery.domain.enums import UpdateState
from tg_lottery.domain.rules import CampaignRules, canonical_hash
from tg_lottery.infrastructure.db import (
    DrawSnapshot,
    Participant,
    PollingOffset,
    QualificationCheck,
    ScheduledJob,
    TelegramUpdate,
    UserProfile,
)

CURSOR_KEY = "platform_qualification_review"
PENDING = (UpdateState.RECEIVED, UpdateState.PROCESSING, UpdateState.FAILED)


def relevant_inbox(campaign, user_ids, *, chat_ids):
    policy = CampaignRules.model_validate(campaign.rules).platform_policy
    return select(TelegramUpdate).where(
        TelegramUpdate.bot_id == policy.bot_id,
        ~projected_start_exists(),
        or_(
            (
                (TelegramUpdate.routing_chat_id.in_(chat_ids))
                & TelegramUpdate.update_type.in_(("chat_member", "my_chat_member"))
                & or_(
                    TelegramUpdate.update_type == "my_chat_member",
                    TelegramUpdate.payload["chat_member"]["new_chat_member"]["user"]["id"]
                    .as_integer()
                    .in_(user_ids),
                )
            ),
            (
                (TelegramUpdate.routing_chat_id.in_(user_ids))
                & (
                    (TelegramUpdate.update_type == "my_chat_member")
                    | (
                        (TelegramUpdate.update_type == "message")
                        & TelegramUpdate.payload["message"]["text"]
                        .as_string()
                        .op("~")(r"^/start(?:@[A-Za-z0-9_]+)?(?:\s|$)")
                    )
                )
            ),
        ),
    )


async def begin_platform_review(session, campaign, *, now):
    users = tuple(
        await session.scalars(
            select(Participant.user_id).where(Participant.campaign_id == campaign.id)
        )
    )
    chat_ids = tuple(item.chat_id for item in await qualification_targets(session, campaign))
    inbox = relevant_inbox(campaign, users, chat_ids=chat_ids).subquery()
    barrier = int(await session.scalar(select(func.max(inbox.c.id))) or 0)
    pending = await session.scalar(
        select(inbox.c.id).where(inbox.c.id <= barrier, inbox.c.state.in_(PENDING)).limit(1)
    )
    if pending is not None:
        return None
    previous = campaign.presentation.get(CURSOR_KEY) or {}
    cursor = {
        "id": _review_id(previous, barrier=barrier, chat_ids=chat_ids),
        "inbox_watermark": barrier,
        "started_at": now.isoformat(),
        "complete": False,
        "chat_ids": list(chat_ids),
    }
    campaign.presentation = campaign.presentation | {CURSOR_KEY: cursor}
    return cursor


def _review_id(previous, *, barrier, chat_ids):
    if (
        previous.get("id")
        and previous.get("inbox_watermark") == barrier
        and previous.get("chat_ids") == list(chat_ids)
    ):
        return previous["id"]
    return str(uuid4())


async def reusable_platform_check(session, campaign, participant, *, cursor, now):
    policy = CampaignRules.model_validate(campaign.rules).platform_policy
    issue = await _participant_issue(
        session,
        participant,
        policy=policy,
        cursor=cursor,
        now=now + timedelta(seconds=30),
        campaign=campaign,
    )
    return issue is None


async def finish_platform_review(session, campaign, cursor):
    if campaign.presentation.get(CURSOR_KEY, {}).get("id") == cursor["id"]:
        campaign.presentation = campaign.presentation | {CURSOR_KEY: cursor | {"complete": True}}


async def platform_snapshot_issue(session, campaign, *, now):
    policy = CampaignRules.model_validate(campaign.rules).platform_policy
    if policy is None:
        return None
    cursor = campaign.presentation.get(CURSOR_KEY) or {}
    if not cursor.get("complete"):
        return "platform_review_incomplete"
    participants = tuple(
        await session.scalars(select(Participant).where(Participant.campaign_id == campaign.id))
    )
    issue = await _review_inbox_issue(
        session,
        campaign,
        policy=policy,
        cursor=cursor,
        user_ids=tuple(item.user_id for item in participants),
    )
    if issue:
        return issue
    for participant in participants:
        issue = await _participant_issue(
            session, participant, policy=policy, cursor=cursor, now=now, campaign=campaign
        )
        if issue:
            return issue
    return None


async def _participant_issue(session, participant, *, policy, cursor, now, campaign):
    check = await session.scalar(
        select(QualificationCheck)
        .where(
            QualificationCheck.participant_id == participant.id, QualificationCheck.phase == "draw"
        )
        .order_by(QualificationCheck.observed_at.desc(), QualificationCheck.id.desc())
        .limit(1)
    )
    if check is None or check.state.value not in {"eligible", "ineligible"}:
        return "platform_candidate_unknown"
    if check.error_code == "behavior_gate_incomplete" and not behavior_qualified(
        participant, campaign
    ):
        return None
    if await _region_changed(session, participant, campaign=campaign, check=check):
        return "platform_candidate_region_changed"
    if check.observed_at < now - timedelta(seconds=policy.max_observation_age_seconds):
        return "platform_candidate_observation_expired"
    if check.observations.get("platform_review_id") != cursor["id"]:
        return "platform_candidate_review_changed"
    relation = await relationship_observation(
        session, policy.bot_id, user_id=participant.user_id, lock=True
    )
    if relation != check.observations.get("bot_relation"):
        return "platform_candidate_relationship_changed"
    return None


async def schedule_platform_refresh(uow_factory, campaign_id, *, now):
    from tg_lottery.infrastructure.db import Campaign

    async with uow_factory() as uow:
        campaign = await uow.session.get(Campaign, campaign_id, with_for_update=True)
        if campaign is None or not campaign.rules.get("platform_policy"):
            return
        if now < campaign.cutoff_at:
            return
        if await uow.session.scalar(
            select(DrawSnapshot.id).where(DrawSnapshot.campaign_id == campaign_id)
        ):
            return
        issue = await platform_snapshot_issue(uow.session, campaign, now=now)
        if issue is None:
            return
        active = await uow.session.scalar(
            select(ScheduledJob.id)
            .where(
                ScheduledJob.aggregate_id == str(campaign_id),
                ScheduledJob.job_type == "qualification_review",
                ScheduledJob.state.in_(("pending", "claimed", "retry_scheduled")),
            )
            .limit(1)
        )
        if active is not None:
            return
        fingerprint = canonical_hash(
            {
                "issue": issue,
                "cursor": campaign.presentation.get(CURSOR_KEY),
                "bucket": int(now.timestamp()) // 30,
            }
        )
        key = f"platform_review:{campaign_id}:{fingerprint}"
        if await uow.session.scalar(
            select(ScheduledJob.id).where(ScheduledJob.business_key == key)
        ):
            return
        add_job(
            uow.session,
            job_type="qualification_review",
            aggregate_id=str(campaign_id),
            business_key=key,
            run_at=now,
        )


async def _review_inbox_issue(session, campaign, *, policy, cursor, user_ids):
    poll = await session.get(PollingOffset, policy.bot_id, with_for_update=True)
    if (
        poll is None
        or poll.integrity_status != "ok"
        or poll.last_poll_succeeded_at is None
        or poll.last_poll_succeeded_at < campaign.cutoff_at
    ):
        return "platform_polling_not_fresh"
    inbox = relevant_inbox(campaign, user_ids, chat_ids=tuple(cursor["chat_ids"])).subquery()
    latest = int(await session.scalar(select(func.max(inbox.c.id))) or 0)
    if latest != cursor["inbox_watermark"]:
        return "platform_relationship_inbox_changed"
    pending = await session.scalar(select(inbox.c.id).where(inbox.c.state.in_(PENDING)).limit(1))
    if pending is not None:
        return "platform_relationship_inbox_pending"
    return None


async def _region_changed(session, participant, *, campaign, check):
    if not campaign.rules.get("allowed_regions"):
        return False
    user = await session.get(UserProfile, participant.user_id, with_for_update=True)
    actual_region = user.region if user else None
    return check.observations.get("profile", {}).get("region") != actual_region
