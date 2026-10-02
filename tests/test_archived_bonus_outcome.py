# ruff: noqa: RUF001

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest

from tests.helpers import seed_campaign
from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import add_audit
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.rules import CampaignRules, GroupActivityRule, GroupInviteRule, InviteRule
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    CampaignReferralRoute,
    DrawRun,
    DrawSnapshot,
    InviteLink,
    TelegramOutbox,
)
from tg_lottery.telegram.gateway_invite_delivery import invite_delivery_content
from tg_lottery.telegram.gateway_requests import build_gateway_call
from tg_lottery.telegram.member_bonus_text import bonus_closed_notice, invite_list_prompt
from tg_lottery.telegram.member_bonus_views import (
    build_bonus_category,
    build_bonus_group_detail,
    build_bonus_overview,
)
from tg_lottery.telegram.member_group_invites import build_group_invite_payload
from tg_lottery.telegram.member_invites import legacy_invite_link_payload
from tg_lottery.telegram.member_referral_landing import _landing_text

GROUP_CHAT_ID = -100123
ACTOR_USER_ID = 201
REFERRAL_REF = "AAAAAAAAAAAAAAAAAAAAAA"
RETENTION_HOURS = 1
SCENARIOS = (
    ("drawn", "本场已开奖"),
    ("cancelled", "本场已取消，未开奖"),
    ("both", "本场已开奖"),
    ("unconfirmed", "未确认"),
    ("incomplete", "未确认"),
    ("foreign_cancel", "未确认"),
)


class NoDecryption:
    def decrypt(self, *_args, **_kwargs):
        raise AssertionError("historical invitation views must hide personal links")


@pytest.mark.parametrize(("scenario", "expected"), SCENARIOS)
async def test_archived_outcome_is_consistent_in_bonus_invites_and_delayed_delivery(
    session_factory, *, scenario: str, expected: str
) -> None:
    campaign_id, outbox_id = await _seed_archived_case(session_factory, scenario=scenario)
    queries = QueryService(session_factory)
    campaign = await queries.campaign(campaign_id)
    outcome = await queries.campaign_outcome(campaign_id)
    views = await _bonus_views(campaign, queries=queries)
    landing = await queries.referral_landing_by_reference(REFERRAL_REF)
    landing_text = _landing_text(landing, True, now=datetime.now(UTC), outcome=outcome)
    async with session_factory() as session:
        outbox = await session.get(TelegramOutbox, outbox_id)
        session.expunge(outbox)
    call = await build_gateway_call(session_factory, outbox)
    delivered = invite_delivery_content(call.request, call.invite_link, cipher=NoDecryption())
    for text, markup in [*views, delivered]:
        _assert_outcome_text(text, expected=expected)
        assert not any(
            button.url or button.copy_text for row in markup.inline_keyboard for button in row
        )
    _assert_outcome_text(landing_text, expected=expected)
    assert not call.request.payload["invite_bonus_active"]
    assert not call.request.payload["invite_entry_visible"]
    if outcome.kind == "drawn":
        assert "实际开奖时间" in landing_text
    else:
        assert "计划开奖时间" in landing_text
    async with session_factory() as session:
        assert (await session.get(Campaign, campaign_id)).state is CampaignState.ARCHIVED


def test_missing_outcome_keeps_historical_notice_readable_and_explicit() -> None:
    from types import SimpleNamespace

    campaign = SimpleNamespace(state=CampaignState.ARCHIVED)
    for text in (
        bonus_closed_notice(campaign),
        invite_list_prompt(campaign, retention_hours=RETENTION_HOURS),
    ):
        assert "未确认" in text and "核查" in text
        assert "本场已开奖" not in text


async def _bonus_views(campaign, *, queries):
    dependencies = {
        "user_id": ACTOR_USER_ID,
        "queries": queries,
        "cipher": NoDecryption(),
        "bot_username": "test_lottery_bot",
        "unavailable": {},
    }
    views = [await build_bonus_overview(campaign, **dependencies)]
    for category in ("invites", "activity"):
        views.append(
            await build_bonus_category(campaign, category=category, page=0, **dependencies)
        )
        views.append(
            await build_bonus_group_detail(
                campaign,
                category=category,
                chat_id=GROUP_CHAT_ID,
                return_page=0,
                **dependencies,
            )
        )
    views.append(await build_group_invite_payload(campaign, page=0, **dependencies))
    views.append(
        await legacy_invite_link_payload(
            campaign, user_id=ACTOR_USER_ID, queries=queries, cipher=NoDecryption()
        )
    )
    return views


def _assert_outcome_text(text: str, *, expected: str) -> None:
    assert expected in text
    if expected != "本场已开奖":
        assert "本场已开奖" not in text
    if expected == "未确认":
        assert "核查" in text


async def _seed_archived_case(session_factory, *, scenario: str) -> tuple[UUID, UUID]:
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("invite", "activity"),
        invite=InviteRule(retention_hours=RETENTION_HOURS),
        group_invite_rules={str(GROUP_CHAT_ID): GroupInviteRule()},
        group_activity_rules={str(GROUP_CHAT_ID): GroupActivityRule()},
    )
    async with session_factory() as session, session.begin():
        group, campaign, participant = await seed_campaign(
            session, state=CampaignState.ARCHIVED, rules=rules, cutoff_at=now - timedelta(hours=1)
        )
        campaign.presentation = {
            "participation_groups": [
                {"id": GROUP_CHAT_ID, "title": group.title, "join_url": "https://t.me/test_group"}
            ]
        }
        target = CampaignParticipationGroup(
            campaign_id=campaign.id,
            group_id=group.id,
            position=1,
            is_primary=True,
            chat_id_snapshot=GROUP_CHAT_ID,
            title_snapshot=group.title,
            private=False,
            public_join_url="https://t.me/test_group",
            permission_snapshot={"bot_admin_validated": True},
        )
        session.add(target)
        await session.flush()
        outbox = await _add_invite_delivery(
            session, campaign, participant=participant, target=target
        )
        await _add_history(session, campaign, scenario=scenario, completed_at=now)
        await session.flush()
        return campaign.id, outbox.id


async def _add_invite_delivery(session, campaign, *, participant, target):
    route = CampaignReferralRoute(
        campaign_id=campaign.id,
        participant_id=participant.id,
        participation_group_id=target.id,
        public_ref=REFERRAL_REF,
        bonus_join_deadline=campaign.cutoff_at - timedelta(hours=RETENTION_HOURS),
    )
    session.add(route)
    await session.flush()
    link = InviteLink(
        campaign_id=campaign.id,
        participant_id=participant.id,
        participation_group_id=target.id,
        referral_route_id=route.id,
        link_hash=sha256(str(route.id).encode()).hexdigest(),
        encrypted_url=b"encrypted",
        telegram_name="lottery_history",
    )
    session.add(link)
    await session.flush()
    outbox = TelegramOutbox(
        operation="send_invite_link",
        aggregate_type="invite_link",
        aggregate_id=str(link.id),
        recipient_chat_id=participant.user_id,
        business_key=f"send_invite_link:{link.id}:{participant.user_id}",
        request_hash="a" * 64,
        payload={"invite_link_id": str(link.id), "group_title": target.title_snapshot},
        state=OutboxState.PENDING,
        run_at=datetime.now(UTC),
    )
    session.add(outbox)
    return outbox


async def _add_history(session, campaign, *, scenario: str, completed_at: datetime) -> None:
    if scenario in {"cancelled", "both", "foreign_cancel"}:
        add_audit(
            session,
            actor_user_id=campaign.created_by,
            action="campaign.cancelled",
            aggregate_type="campaign",
            aggregate_id=str(uuid4() if scenario == "foreign_cancel" else campaign.id),
            reason="隔离数据库历史结果回归",
        )
    if scenario not in {"drawn", "both", "incomplete"}:
        return
    snapshot = DrawSnapshot(
        campaign_id=campaign.id,
        config_revision=campaign.config_revision,
        ledger_watermark=campaign.cutoff_at,
        snapshot_hash=sha256(str(campaign.id).encode()).hexdigest(),
        prize_plan_hash=campaign.prize_plan_hash,
        total_weight=1,
    )
    session.add(snapshot)
    await session.flush()
    session.add(
        DrawRun(
            snapshot_id=snapshot.id,
            algorithm_version="test_history_v1",
            random_input="test_history_input",
            started_by=campaign.created_by,
            completed_at=None if scenario == "incomplete" else completed_at,
        )
    )
