"""Durable channel promotion intents, independent of group publication facts."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert

from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.enums import CampaignState, JobState, OutboxState
from tg_lottery.infrastructure.db import Campaign, Group, Prize, ScheduledJob, TelegramOutbox

FEATURED_JOB = "featured_channel_scan"
FEATURED_SEND = "send_featured_campaign"
DIGEST_SEND = "send_featured_digest"
BEIJING = ZoneInfo("Asia/Shanghai")
OPEN_STATES = (CampaignState.RUNNING, CampaignState.SCHEDULED)


class FeaturedChannelService:
    def __init__(
        self,
        session_factory,
        *,
        chat_id: int,
        bot_username: str,
        digest_hour: int = 10,
        verify_target=None,
    ):
        if chat_id >= 0 or not 0 <= digest_hour <= 23:
            raise ValueError("featured channel requires a negative chat id and hour 0..23")
        self.factory = session_factory
        self.chat_id = chat_id
        self.bot_username = bot_username
        self.digest_hour = digest_hour
        self.verify_target = verify_target

    async def ensure_job(self, now: datetime) -> None:
        async with self.factory() as session, session.begin():
            await self._lock(session)
            active = await session.scalar(
                select(ScheduledJob.id).where(
                    ScheduledJob.job_type == FEATURED_JOB,
                    ScheduledJob.state.in_(
                        (JobState.PENDING, JobState.CLAIMED, JobState.RETRY_SCHEDULED)
                    ),
                )
            )
            if active is None:
                await self._schedule(session, now)

    async def run(self, job) -> None:
        if job.aggregate_id != "system":
            raise ValueError("featured scan requires system aggregate")
        if self.verify_target is not None:
            await self.verify_target()
        now = utcnow()
        async with self.factory() as session, session.begin():
            await self._lock(session)
            campaigns = tuple(await session.scalars(self._eligible(now)))
            known = set(
                await session.scalars(
                    select(TelegramOutbox.business_key).where(
                        TelegramOutbox.operation == FEATURED_SEND,
                        TelegramOutbox.recipient_chat_id == self.chat_id,
                    )
                )
            )
            for campaign in campaigns:
                await self._queue_campaign(session, campaign, known=known)
            await self._queue_digest(session, campaigns=campaigns, now=now)
            await self._queue_states(session, now)
            await self._schedule(
                session, now + timedelta(seconds=30), key=f"{FEATURED_JOB}:after:{job.id}"
            )

    async def _lock(self, session):
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(FEATURED_JOB))))

    async def _schedule(self, session, run_at, *, key=None):
        statement = insert(ScheduledJob).values(
            job_type=FEATURED_JOB,
            aggregate_id="system",
            business_key=key or f"{FEATURED_JOB}:{run_at.isoformat()}",
            payload={},
            run_at=run_at,
            state=JobState.PENDING,
            attempts=0,
        )
        await session.execute(statement.on_conflict_do_nothing(index_elements=["business_key"]))

    def _eligible(self, now):
        published = exists().where(
            TelegramOutbox.aggregate_id == func.cast(Campaign.id, TelegramOutbox.aggregate_id.type),
            TelegramOutbox.operation == "send_campaign_card",
            TelegramOutbox.state == OutboxState.CONFIRMED,
            TelegramOutbox.remote_message_id.is_not(None),
            TelegramOutbox.recipient_chat_id != self.chat_id,
        )
        return (
            select(Campaign)
            .where(Campaign.state.in_(OPEN_STATES), Campaign.cutoff_at > now, published)
            .order_by(Campaign.cutoff_at, Campaign.id)
        )

    async def _queue_campaign(self, session, campaign, *, known):
        key = f"featured:{self.chat_id}:{campaign.id}"
        if key in known:
            return
        item = await self._summary(session, campaign)
        self._add(
            session, key=key, operation=FEATURED_SEND, items=[item], aggregate=str(campaign.id)
        )

    async def _queue_digest(self, session, *, campaigns, now):
        local = now.astimezone(BEIJING)
        if local.hour < self.digest_hour or not campaigns:
            return
        prefix = f"featured_digest:{self.chat_id}:{local.date().isoformat()}:"
        if await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.business_key.startswith(prefix)).limit(1)
        ):
            return
        items = [await self._summary(session, campaign) for campaign in campaigns]
        for offset in range(0, len(items), 4):
            self._add(
                session,
                key=prefix + str(offset // 4),
                operation=DIGEST_SEND,
                items=items[offset : offset + 4],
                aggregate="system",
                date=local.date().isoformat(),
            )

    async def _summary(self, session, campaign):
        prizes = tuple(
            await session.scalars(
                select(Prize)
                .where(Prize.campaign_id == campaign.id)
                .order_by(Prize.position)
                .limit(3)
            )
        )
        groups = campaign.presentation.get("participation_groups") or []
        if not groups:
            group = await session.get(Group, campaign.group_id)
            groups = [{"title": group.title}] if group else []
        return {
            "campaign_id": str(campaign.id),
            "campaign_ref": campaign.public_ref,
            "name": campaign.name[:80],
            "prizes": "、".join(f"{p.name[:40]} x{p.winner_count}" for p in prizes),
            "groups": "、".join(str(g.get("title") or "群聊")[:30] for g in groups[:3]),
            "cutoff_at": campaign.cutoff_at.isoformat(),
            "draw_at": campaign.draw_at.isoformat(),
            "full_draw": campaign.rules.get("draw_mode") == "full",
        }

    def _add(self, session, *, key, operation, items, aggregate, date=None):
        add_outbox(
            session,
            operation=operation,
            aggregate_type="campaign" if operation == FEATURED_SEND else "system",
            aggregate_id=aggregate,
            recipient_chat_id=self.chat_id,
            business_key=key,
            payload={"items": items, "bot_username": self.bot_username, "date": date},
        )

    async def _queue_states(self, session, now):
        edits = await session.scalars(
            select(TelegramOutbox)
            .where(
                TelegramOutbox.operation == "edit_featured_campaign",
                TelegramOutbox.recipient_chat_id == self.chat_id,
            )
            .order_by(TelegramOutbox.created_at, TelegramOutbox.id)
        )
        latest = {row.payload["message_id"]: row for row in edits}
        rows = await session.execute(
            select(TelegramOutbox, Campaign)
            .join(
                Campaign,
                func.cast(Campaign.id, TelegramOutbox.aggregate_id.type)
                == TelegramOutbox.aggregate_id,
            )
            .where(
                TelegramOutbox.operation == FEATURED_SEND,
                TelegramOutbox.recipient_chat_id == self.chat_id,
                TelegramOutbox.state == OutboxState.CONFIRMED,
                TelegramOutbox.remote_message_id.is_not(None),
            )
        )
        for row, campaign in rows:
            await self._queue_state(
                session,
                row=row,
                campaign=campaign,
                now=now,
                latest=latest.get(row.remote_message_id),
            )

    async def _queue_state(self, session, *, row, campaign, now, latest):
        state = promotion_state(campaign, now)
        previous = latest.payload["items"][0].get("state", "open") if latest else "open"
        if state == previous:
            return
        key = f"featured_state:{row.id}:{state}:{latest.id if latest else 'initial'}"
        payload = row.payload | {"items": [row.payload["items"][0] | {"state": state}]}
        add_outbox(
            session,
            operation="edit_featured_campaign",
            aggregate_type="campaign",
            aggregate_id=str(campaign.id),
            recipient_chat_id=self.chat_id,
            business_key=key,
            payload=payload | {"message_id": row.remote_message_id},
        )


def promotion_state(campaign, now):
    if campaign.state not in OPEN_STATES:
        return campaign.state.value
    return "closed" if campaign.cutoff_at <= now else "open"
