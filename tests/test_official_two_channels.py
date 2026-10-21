from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from tests import test_v33_platform_runtime as runtime
from tests.test_v33_platform_policy import policy as legacy_policy
from tg_lottery.application.bot_relationships import BotRelationshipService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.config import Settings
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.platform_policy import PlatformPolicy
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Campaign, CampaignRequirement, Participant
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.rule_capabilities import SUPPORTED_RULE_VERSIONS, require_capabilities
from tg_lottery.telegram.platform_qualification import observe_campaign_memberships
from tg_lottery.telegram.platform_requirements import platform_summary


def two_channel_policy():
    return PlatformPolicy(
        version="official-v2",
        bot_id=999,
        channels=tuple(row for row in legacy_policy().channels if row.role != "guide"),
    )


def test_current_policy_requires_exactly_navigation_and_featured_and_preserves_v1():
    current = two_channel_policy()
    assert {row.role for row in current.channels} == {"navigation", "featured"}
    assert len(PlatformPolicy.model_validate(legacy_policy().model_dump()).channels) == 3
    for channels in (current.channels[:1], legacy_policy().channels):
        with pytest.raises(ValidationError, match="official channel roles"):
            PlatformPolicy(version="official-v2", bot_id=999, channels=channels)
    with pytest.raises(ValidationError, match="official channel roles"):
        PlatformPolicy(bot_id=999, channels=current.channels)
    require_capabilities(("platform:official-v2",), SUPPORTED_RULE_VERSIONS)
    with pytest.raises(RuntimeError, match="official-v2"):
        require_capabilities(("platform:official-v2",), {"platform:official-v1"})
    summary = platform_summary({"platform_policy": current.model_dump(mode="json")})
    assert "关注 2 个" in summary and "guide" not in summary


def test_enabled_settings_use_two_channel_policy_without_inferring_help_targets():
    settings = Settings(
        _env_file=None,
        bot_token="999:" + "a" * 30,
        database_url="unused",
        app_encryption_key="a" * 43,
        platform_required_enabled=True,
        platform_official_channels=two_channel_policy().channels,
        guide_channel_url="https://t.me/old_guide",
    )
    assert settings.platform_policy == two_channel_policy()


@pytest.mark.parametrize(
    ("started", "featured_status", "expected"),
    [(False, "member", "ineligible"), (True, "left", "ineligible"), (True, "member", "eligible")],
)
async def test_both_channels_and_bot_start_are_required(
    session_factory, *, started, featured_status, expected
):
    policy = two_channel_policy()
    relationships = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    if started:
        await relationships.record(201, datetime.now(UTC), started=True)
    calls = []

    async def membership(chat_id, user_id):
        calls.append((chat_id, user_id))
        state = "member" if chat_id == policy.channels[0].chat_id else featured_status
        return SimpleNamespace(status="administrator" if user_id == 999 else state)

    rules = CampaignRules(platform_policy=policy, required_chat_ids=policy.chat_ids)
    result = await observe_campaign_memberships(
        SimpleNamespace(id=999, get_chat_member=membership),
        rules=rules,
        chat_ids=policy.chat_ids,
        user_id=201,
        observed_at=datetime.now(UTC),
        relationship_loader=relationships.observation,
    )
    assert result.state.value == expected
    assert {chat_id for chat_id, _user in calls} == set(policy.chat_ids)


async def test_two_channel_draw_review_freezes_eligible_weight(session_factory, monkeypatch):
    monkeypatch.setattr(runtime, "policy", two_channel_policy)
    cid, pid = await runtime.seed_platform(session_factory)
    relations = BotRelationshipService(partial(UnitOfWork, session_factory), bot_id=999)
    await relations.record(201, datetime.now(UTC), started=True)
    await runtime.review(session_factory).run(runtime.job(cid))
    snapshot = await SettlementService(partial(UnitOfWork, session_factory)).settle(
        cid, datetime.now(UTC)
    )
    assert snapshot.total_weight == 1
    async with session_factory() as session:
        assert (await session.get(Campaign, cid)).rules["platform_policy"][
            "version"
        ] == "official-v2"
        assert set(await session.scalars(select(CampaignRequirement.chat_id_snapshot))) == set(
            two_channel_policy().chat_ids
        )
        assert (
            await session.get(Participant, pid)
        ).qualification_state == QualificationState.ELIGIBLE
