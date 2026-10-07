import html
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_v33_permission_and_card_repair import card_payload
from tests.test_v33_unlimited_creation import current_draft
from tg_lottery.config import Settings
from tg_lottery.domain.platform_policy import OfficialChannel, PlatformPolicy
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.campaign_card import condition_chats
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.presentation.participation_rules import rule_manifest, split_rule_blocks
from tg_lottery.rule_capabilities import SUPPORTED_RULE_VERSIONS, require_capabilities
from tg_lottery.telegram.creator_bonus_policy import current_bonus_policy
from tg_lottery.telegram.creator_reuse import without_old_platform
from tg_lottery.telegram.draft_config import rules_from_data
from tg_lottery.telegram.member_condition_views import conditions
from tg_lottery.telegram.platform_requirements import (
    attach_platform_policy,
    editable_requirement_data,
    frozen_requirement_data,
    inspect_official_channels,
)


def policy():
    return PlatformPolicy(
        bot_id=999,
        channels=tuple(
            OfficialChannel(
                role=role,
                chat_id=-10001 - index,
                title=role,
                join_url=f"https://t.me/official_{role}",
            )
            for index, role in enumerate(("guide", "navigation", "featured"))
        ),
    )


def test_platform_configuration_is_explicit_and_never_inferred_from_help_urls():
    values = dict(
        bot_token="999:" + "a" * 30,
        database_url="unused",
        app_encryption_key="a" * 43,
        guide_channel_url="https://t.me/helpname/12",
    )
    assert Settings(_env_file=None, **values).platform_policy is None
    with pytest.raises(ValidationError, match="all three"):
        Settings(_env_file=None, **values, platform_required_enabled=True)
    enabled = Settings(
        _env_file=None,
        **values,
        platform_required_enabled=True,
        platform_official_channels=policy().channels,
    )
    assert enabled.platform_policy == policy()
    channels = list(policy().channels)
    channels[1] = channels[1].model_copy(update={"chat_id": channels[0].chat_id})
    with pytest.raises(ValidationError, match="distinct"):
        PlatformPolicy(bot_id=999, channels=tuple(channels))


def test_new_draft_freezes_all_official_targets_and_preserves_duplicate_sources():
    original = current_draft() | {"required_chat_ids": [-10001], "required_chat_refs": [-10001]}
    data = attach_platform_policy(original, policy())
    rules = rules_from_data(data)
    assert set(policy().chat_ids) <= set(rules.required_chat_ids)
    editable = editable_requirement_data(data, policy())
    assert editable["required_chat_ids"] == []
    frozen = frozen_requirement_data(editable, [], policy())
    assert len(frozen["required_chats"]) == 3
    assert frozen["required_chats"][0]["sources"] == ["platform", "creator"]
    from tg_lottery.telegram.creator_participation import participation_text

    assert "@official_guide" in participation_text(frozen)
    original_copy = deepcopy(frozen)
    reused = without_old_platform(frozen)
    assert reused["required_chat_ids"] == [-10001]
    assert reused["platform_policy"] is None
    assert frozen == original_copy
    with pytest.raises(ValidationError, match="all frozen"):
        CampaignRules.model_validate(rules.model_dump() | {"required_chat_ids": []})


async def test_official_permissions_only_require_bot_admin_and_match_channel_identity():
    calls = []

    class Bot:
        id = 999

        async def get_chat(self, chat_id):
            channel = next(row for row in policy().channels if row.chat_id == chat_id)
            return SimpleNamespace(id=chat_id, type="channel", username=f"official_{channel.role}")

        async def get_chat_member(self, chat_id, user_id):
            calls.append(user_id)
            return SimpleNamespace(status="administrator")

    await inspect_official_channels(Bot(), policy())
    assert calls == [999, 999, 999]


def test_private_links_resolve_exact_same_condition_order_with_official_targets():
    payload = card_payload()
    payload["presentation"]["participation_groups"][0]["private"] = True
    payload["presentation"]["participation_groups"][0]["join_url"] = "https://t.me/+private_secret"
    raw = payload["rules"] | {
        "platform_policy": policy().model_dump(mode="json"),
        "required_chat_ids": list(policy().chat_ids),
    }
    payload["rules"] = raw
    payload["presentation"]["required_chats"] = [row.record() for row in policy().channels]
    public = condition_chats(payload)
    private = conditions(SimpleNamespace(presentation=payload["presentation"], rules=raw))
    assert [row["id"] for row in public] == [row["id"] for row in private]
    caption, _ = rule_manifest(payload)
    assert "启动本抽奖机器人" in caption
    assert f"condition_{payload['campaign_ref']}_4" in caption
    assert "private_secret" not in caption


def test_long_html_urls_use_visible_capacity_and_large_descriptions_split_safely():
    block = '<a href="https://t.me/' + "x" * 8000 + '">群名 &amp; 😀</a>'
    assert split_rule_blocks((block,)) == (block,)
    payload = card_payload()
    payload["presentation"]["description"] = "😀<&>" * 3000
    _, pages = rule_manifest(payload)
    assert len(pages) > 1
    assert all(html_utf16_length(page) <= 4096 for page in pages)
    assert all(html.escape(payload["name"]) in page and "返回本场活动" in page for page in pages)
    assert sum(page.count("😀&lt;&amp;&gt;") for page in pages) == 3000


def test_partial_bonus_templates_receive_model_defaults_without_changing_rewards():
    data = current_draft() | {"group_activity_rules": {"-100123": {"messages_per_cycle": 7}}}
    value = current_bonus_policy(data)["group_activity_rules"]["-100123"]
    assert value["enabled"] and value["reward_tickets"] == 1 and value["messages_per_cycle"] == 7
    assert value["daily_cap_mode"] == "unlimited" and value["daily_max_cycles"] is None


def test_rollback_requires_both_platform_and_unlimited_capabilities():
    require_capabilities(("platform:official-v1", "bonus:unlimited-v1"), SUPPORTED_RULE_VERSIONS)
    with pytest.raises(RuntimeError, match="platform"):
        require_capabilities(("platform:official-v1",), {"legacy", "v3.0"})


async def test_publication_rechecks_changed_presentation_and_requires_new_confirmation():
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from aiogram.types import CallbackQuery, Chat, Message, User

    from tg_lottery.telegram.creator_publication import CreatorPublisher

    data = current_draft()
    sessions = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(state="draft:publish_confirm", data=data)),
        set=AsyncMock(),
    )
    campaigns = SimpleNamespace(platform_policy=None, create_draft=AsyncMock())
    renderer = SimpleNamespace(render=AsyncMock())
    publisher = CreatorPublisher(
        bot=object(),
        bot_username="lottery_bot",
        sessions=sessions,
        campaigns=campaigns,
        queries=object(),
        profiles=object(),
        renderer=renderer,
    )
    publisher._resolve = AsyncMock(return_value=data | {"issuer_name": "身份已更新"})
    user = User(id=201, is_bot=False, first_name="创建者")
    message = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=201, type="private"))
    query = CallbackQuery(id="1", from_user=user, chat_instance="1", message=message)
    await publisher.publish(query, None)
    campaigns.create_draft.assert_not_awaited()
    assert sessions.set.call_args.args[1] == "draft:publish_confirm"
    assert "已变化" in renderer.render.call_args.kwargs["text"]
