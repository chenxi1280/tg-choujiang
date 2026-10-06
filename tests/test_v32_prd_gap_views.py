# ruff: noqa: RUF001
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from tests.test_v23_bonus_navigation import BonusQueries, _bonus_state
from tests.test_v32_member_navigation import REFERENCE, USER_ID, _detail, _navigation, _query
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.application.creator_campaign_pages import CreatorCampaignPage
from tg_lottery.application.operational_issue_queries import OperationalIssue
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.creator_list_views import campaign_origin, creator_list_view
from tg_lottery.telegram.member_condition_check import condition_check_payload
from tg_lottery.telegram.member_navigation_context import MemberPageContext
from tg_lottery.telegram.member_navigation_views import participant_detail_view
from tg_lottery.telegram.member_profile_conditions import profile_conditions


@pytest.mark.parametrize("category", ["invites", "activity"])
@pytest.mark.parametrize("overview", [False, True])
async def test_skipped_single_group_refresh_returns_to_actual_activity(
    monkeypatch, category, overview
):
    campaign, progress, statuses = _bonus_state()
    rules = CampaignRules.model_validate(campaign.rules)
    selected = "invite" if category == "invites" else category
    campaign.rules = rules.model_copy(
        update={
            "bonus_types": (selected,),
            "group_invite_rules": rules.group_invite_rules if category == "invites" else {},
            "group_activity_rules": rules.group_activity_rules if category == "activity" else {},
        }
    ).model_dump(mode="json")
    queries = BonusQueries(progress, statuses)
    queries.participant_campaign_detail = AsyncMock(
        return_value=replace(_detail(), qualification=QualificationState.ELIGIBLE)
    )
    flow, _, tokens = _navigation()
    flow._queries = queries
    flow._cipher = SimpleNamespace(decrypt=lambda *_args, **_kwargs: "https://t.me/+test-link")
    flow._campaign = AsyncMock(return_value=campaign)
    monkeypatch.setattr(
        "tg_lottery.telegram.member_navigation.inspect_bonus_permissions",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(Message, "edit_text", AsyncMock())
    route = f"m:bo:{REFERENCE}" if overview else f"member:bonus_{category}:{REFERENCE}:0"
    parent = "m:detail:" + REFERENCE
    await flow._bonus(_query(), MemberPageContext(route, (parent,)))
    requests = tokens.create_many.await_args.kwargs["requests"]
    kind = "id" if category == "invites" else "ad"
    refresh = next(r.payload for r in requests if r.payload["route"].startswith(f"m:{kind}:"))
    assert refresh["trail"] == [parent]
    await flow.handle_grant(
        _query(), CallbackGrant("member:navigate", None, refresh), personal=object()
    )
    returned = tokens.create_many.await_args.kwargs["requests"]
    back = next(r.payload for r in returned if r.payload["back"])
    assert back["route"] == parent and back["trail"] == []


@pytest.mark.parametrize(
    "state",
    [CampaignState.DRAWN, CampaignState.CANCELLED, CampaignState.SETTLING, CampaignState.PAUSED],
)
def test_unavailable_share_and_finished_invitation_tasks_keep_history(state):
    now = datetime.now(UTC)
    detail = replace(_detail(), state=state, cutoff_at=now - timedelta(minutes=1))
    pages, keyboard = participant_detail_view(detail, user_id=USER_ID, bot_username="test_bot")
    buttons = [b for row in keyboard.inline_keyboard for b in row]
    assert not any(b.text in {"我的邀请链接", "分享活动卡片"} for b in buttons)
    assert any(b.text == "邀请记录" for b in buttons)
    assert "历史累计" in "\n".join(pages) or "本期已截止" in "\n".join(pages)


def test_unknown_qualification_keeps_ledger_and_points_to_condition_check():
    detail = replace(
        _detail(),
        qualification=QualificationState.UNKNOWN,
        cutoff_at=datetime.now(UTC) + timedelta(hours=1),
    )
    pages, keyboard = participant_detail_view(detail, user_id=USER_ID, bot_username="test_bot")
    assert keyboard.inline_keyboard[0][0].text == "核查参与条件"
    assert "当前有效次数：待核查" in pages[0] and "累计次数（含暂存）: 5" in pages[0]


def test_invitation_gate_after_join_deadline_points_to_existing_records():
    now = datetime.now(UTC)
    detail = replace(
        _detail(),
        qualification=QualificationState.ELIGIBLE,
        base_qualified=False,
        admission_type="invite",
        cutoff_at=now + timedelta(hours=1),
        invite_join_deadline=now - timedelta(seconds=1),
    )
    pages, keyboard = participant_detail_view(detail, user_id=USER_ID, bot_username="test_bot")
    assert keyboard.inline_keyboard[0][0].text == "查看待核验邀请记录"
    assert "最晚计奖入群时间已过" in pages[0]


@pytest.mark.parametrize("locked", [False, True])
async def test_profile_check_distinguishes_editable_window_and_locked_mismatch(locked):
    now = datetime.now(UTC)
    profile = SimpleNamespace(
        region="上海",
        region_editable_until=now + timedelta(minutes=-1 if locked else 1),
        region_locked_until=now + timedelta(days=30),
    )
    profiles = SimpleNamespace(get=AsyncMock(return_value=profile))
    query = _query().model_copy(
        update={"from_user": _query().from_user.model_copy(update={"username": "verified_member"})}
    )
    lines, rows = await profile_conditions(
        profiles,
        query,
        rules=CampaignRules(username_required=True, allowed_regions=("北京",)),
        reference=REFERENCE,
    )
    text = "\n".join(lines)
    assert "本人用户名：@verified_member（已满足）" in text
    assert ("当前锁定期间无法修改" in text) is locked
    assert bool(rows) is not locked


@pytest.mark.parametrize("registered", [False, True])
async def test_all_operational_block_sources_are_visible_without_blocking_existing_read(registered):
    campaign, _, _ = _bonus_state()
    issue = OperationalIssue(
        -1001, "故障验收群", "bot_not_member", "bot is not a member", "telegram_outbox"
    )
    queries = SimpleNamespace(
        qualification_targets=AsyncMock(return_value=()),
        participant_campaign_detail=AsyncMock(return_value=_detail() if registered else None),
        operational_issues=AsyncMock(return_value=(issue,)),
    )
    profiles = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(region=None)))
    text, keyboard = await condition_check_payload(
        _query(),
        campaign=campaign,
        queries=queries,
        bot=object(),
        profiles=profiles,
        cipher=object(),
    )
    assert "故障验收群" in text and "机器人已不在该群" in text
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert not any(c.startswith("member:join:") for c in callbacks)
    assert any(c.startswith("member:verify:") for c in callbacks) is registered


async def test_each_long_rule_page_keeps_campaign_identity_version_and_join(monkeypatch):
    campaign, _, _ = _bonus_state()
    campaign.name, campaign.config_revision = "完整活动名称<验收>", 7
    flow, queries, _ = _navigation()
    flow._campaign = AsyncMock(return_value=campaign)
    queries.participant_campaign_detail.return_value = None
    queries.operational_issues = AsyncMock(return_value=())
    queries.prizes = AsyncMock(
        return_value=tuple(SimpleNamespace(name="完整奖品" * 20, winner_count=1) for _ in range(40))
    )
    edit = AsyncMock()
    monkeypatch.setattr(Message, "edit_text", edit)
    await flow._rules(_query(), MemberPageContext(f"m:rules:{REFERENCE}:1"))
    text = edit.await_args.args[0]
    assert "完整活动名称&lt;验收&gt;" in text and "规则版本：7" in text
    assert "第2/" in text and f"start=join_{campaign.public_ref}" in text
    keyboard = edit.await_args.kwargs["reply_markup"]
    assert any(b.text == "登记参与" for row in keyboard.inline_keyboard for b in row)


def test_creator_categories_and_origin_distinguish_lifecycle_and_stable_anchor():
    result = CreatorCampaignPage("active", 0, 1, 0, ())
    _, keyboard = creator_list_view(result)
    categories = [
        b.text.removeprefix("✅ ")
        for row in keyboard.inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("nav:created:")
    ]
    assert categories == ["发布中", "进行中", "待开奖", "已结束"]
    for category, code in (("publishing", "p"), ("pending", "w"), ("ended", "e")):
        ref, origin = campaign_origin(f"{REFERENCE}:c:{code}:2")
        assert ref == REFERENCE and origin == f"nav:created:{category}:2:{REFERENCE}"


def test_old_unconfirmed_filter_still_renders_after_last_issue_is_resolved():
    result = CreatorCampaignPage("unconfirmed", 0, 1, 0, ())
    text, keyboard = creator_list_view(result)
    assert "当前分类：待核查" in text and "当前分类暂无活动" in text
    assert any(b.text == "返回首页" for row in keyboard.inline_keyboard for b in row)
