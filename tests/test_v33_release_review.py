import asyncio
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.test_creator_publication_receipt import publisher_fixture
from tests.test_v33_group_reputation import campaign
from tests.test_v33_platform_runtime import MembershipBot, job, review, seed_platform
from tests.v30_helpers import FIRST, rules_for
from tg_lottery.application.bot_relationships import BotRelationshipService
from tg_lottery.domain.enums import CampaignState, UpdateState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign, QualificationCheck, TelegramUpdate
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.jobs.errors import JobRetry
from tg_lottery.jobs.platform_review_batch import review_batch
from tg_lottery.telegram.admin_flow import AdminFlow
from tg_lottery.telegram.group_reputation import _next_reward_notice, _rules
from tg_lottery.telegram.qualification import SmoothRateLimiter


async def test_resume_publication_rechecks_frozen_channels_before_queueing(monkeypatch):
    publisher, _ = publisher_fixture()
    verify = AsyncMock()
    monkeypatch.setattr("tg_lottery.telegram.creator_publication.verify_campaign_admin", verify)
    publisher._verify_frozen_platform = AsyncMock(side_effect=UserFacingError("频道权限已丢失"))
    original = SimpleNamespace(id=uuid4(), state=CampaignState.PUBLISHING)
    with pytest.raises(UserFacingError, match="频道权限"):
        await publisher.resume_message(SimpleNamespace(), user_id=201, data={}, campaign=original)
    publisher._verify_frozen_platform.assert_awaited_once_with(original)
    publisher._campaigns.publish.assert_not_awaited()


async def test_explicit_publication_retry_checks_frozen_policy(monkeypatch):
    from tests.test_v33_platform_policy import policy

    flow = AdminFlow(
        bot=object(),
        creator=None,
        tokens=None,
        drawing=None,
        winners=None,
        campaigns=SimpleNamespace(retry_failed_publication=AsyncMock()),
        queries=SimpleNamespace(
            campaign=AsyncMock(
                return_value=SimpleNamespace(
                    rules={"platform_policy": policy().model_dump(mode="json")}
                )
            )
        ),
    )
    flow._verify_admin = AsyncMock()
    inspect = AsyncMock(side_effect=UserFacingError("频道不可读"))
    monkeypatch.setattr("tg_lottery.telegram.admin_flow.inspect_official_channels", inspect)
    grant = SimpleNamespace(campaign_id=uuid4(), payload={})
    with pytest.raises(UserFacingError, match="频道不可读"):
        await flow._retry_publication(SimpleNamespace(from_user=SimpleNamespace(id=201)), grant)
    assert inspect.await_args.args[1] == policy()
    flow._campaigns.retry_failed_publication.assert_not_awaited()


def test_old_capped_activity_query_does_not_promise_another_reward_today():
    rules = rules_for("message", ("activity", "invite"))
    rule = rules.activity_rule_for(FIRST).model_copy(
        update={"daily_cap_mode": "limited", "daily_max_cycles": 1}
    )
    text = _next_reward_notice(campaign(), rule, needed=1, awarded_today=rule.reward_tickets)
    assert "今日活跃奖励已达" in text and "下一轮" not in text
    text = _rules(campaign(), rules, FIRST)
    assert f"入群满{rules.invite.retention_hours}小时" in text
    assert "新场入群满24小时" not in text


async def test_concurrent_limiter_does_not_reuse_a_future_request_slot():
    now = [0.0]
    slots = []

    async def sleep(seconds):
        await asyncio.sleep(0)
        now[0] += seconds

    limiter = SmoothRateLimiter(10, sleep=sleep, clock=lambda: now[0])

    async def acquire():
        await limiter.acquire()
        slots.append(now[0])

    await asyncio.gather(*(acquire() for _ in range(5)))
    assert slots == pytest.approx([0, 0.1, 0.2, 0.3, 0.4])


async def test_batch_is_bounded_and_preserves_successes_when_one_worker_fails():
    active, peak, done = 0, 0, set()

    async def process(item):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        if item == 0:
            raise RuntimeError("retryable failure")
        done.add(item)

    with pytest.raises(RuntimeError, match="retryable failure"):
        await review_batch(list(range(15)), review=process)
    assert peak == 4 and done == set(range(1, 15))


async def test_retry_reuses_fresh_success_and_ignores_unrelated_member_inbox(session_factory):
    campaign_id, _ = await seed_platform(session_factory)
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await relationships.record(201, datetime.now(UTC), started=True)
    service = review(session_factory)
    await service.run(job(campaign_id))
    async with session_factory() as session, session.begin():
        current = await session.get(Campaign, campaign_id)
        cursor_id = current.presentation["platform_qualification_review"]["id"]
        session.add(
            TelegramUpdate(
                bot_id=999,
                update_id=42,
                update_type="chat_member",
                routing_chat_id=-10001,
                event_at=datetime.now(UTC),
                payload_hash="b" * 64,
                state=UpdateState.RECEIVED,
                payload={"chat_member": {"new_chat_member": {"user": {"id": 987654}}}},
            )
        )
    await service.run(job(campaign_id))
    async with session_factory() as session, session.begin():
        current = await session.get(Campaign, campaign_id)
        assert current.presentation["platform_qualification_review"]["id"] == cursor_id
        checks = tuple(
            await session.scalars(
                select(QualificationCheck).where(
                    QualificationCheck.observed_status == "platform:eligible"
                )
            )
        )
        assert len(checks) == 1
        incoming = await session.scalar(select(TelegramUpdate))
        incoming.payload = {"chat_member": {"new_chat_member": {"user": {"id": 201}}}}
    with pytest.raises(JobRetry, match="Inbox"):
        await service.run(job(campaign_id))


async def test_bot_losing_admin_during_review_prevents_completion(session_factory):
    campaign_id, _ = await seed_platform(session_factory)
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await relationships.record(201, datetime.now(UTC), started=True)

    class RevokedBot(MembershipBot):
        async def get_chat_member(self, chat_id, user_id):
            result = await super().get_chat_member(chat_id, user_id)
            if user_id != self.id:
                self.bot_status = "member"
            return result

    with pytest.raises(JobRetry, match="cannot degrade"):
        await review(session_factory, bot=RevokedBot()).run(job(campaign_id))
    async with session_factory() as session:
        current = await session.get(Campaign, campaign_id)
        assert current.presentation["platform_qualification_review"]["complete"] is False
