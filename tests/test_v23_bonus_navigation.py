from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts
from tg_lottery.application.participant_records import CampaignInvitationRecords
from tg_lottery.application.query_invites import GroupInviteLinkStatus
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules, GroupActivityRule, GroupInviteRule, InviteRule
from tg_lottery.telegram.member_bonus_views import (
    build_bonus_category,
    build_bonus_group_detail,
    build_bonus_overview,
)
from tg_lottery.telegram.member_group_invites import build_group_invite_payload
from tg_lottery.telegram.member_invites import legacy_invite_link_payload
from tg_lottery.telegram.member_records import _invitation_keyboard, _record_keyboard
from tg_lottery.telegram.ui_style import plain_label


class BonusQueries:
    def __init__(
        self,
        progress: object,
        statuses: dict[int, object],
        *,
        targets: tuple[object, ...] = (),
        outcome: CampaignOutcomeFacts | None = None,
    ) -> None:
        self._progress = progress
        self._statuses = statuses
        self._targets = targets
        self._outcome = outcome or CampaignOutcomeFacts("unconfirmed")

    async def campaign_outcome(self, campaign_id):
        return self._outcome

    async def group_bonus_progress(self, campaign_id, user_id):
        return self._progress

    async def group_invite_link_statuses(self, campaign_id, user_id):
        return self._statuses

    async def qualification_targets(self, campaign_id):
        return self._targets


async def test_v23_bonus_navigation_separates_categories_and_group_details() -> None:
    campaign, progress, statuses = _bonus_state()
    queries = BonusQueries(progress, statuses)
    cipher = SimpleNamespace(decrypt=lambda *_args, **_kwargs: "https://t.me/+raw-link")
    common = {
        "user_id": 201,
        "queries": queries,
        "cipher": cipher,
        "bot_username": "lottery_bot",
        "unavailable": {},
    }

    overview, overview_keyboard = await build_bonus_overview(campaign, **common)
    invite_detail, invite_keyboard = await build_bonus_group_detail(
        campaign, category="invites", chat_id=-1001, return_page=0, **common
    )
    activity_detail, activity_keyboard = await build_bonus_group_detail(
        campaign, category="activity", chat_id=-1001, return_page=0, **common
    )

    callbacks = [
        button.callback_data for row in overview_keyboard.inline_keyboard for button in row
    ]
    assert "双加成活动" in overview
    assert "AbCdEf" not in overview
    assert callbacks[:2] == [
        "member:bonus_invites:AbCdEfGhIjKlMnOpQrSt-_:0",
        "member:bonus_activity:AbCdEfGhIjKlMnOpQrSt-_:0",
    ]
    assert "每 2 名有效新成员 +1" in invite_detail
    assert "不设上限" in invite_detail
    assert "距下一次还差 1 人" in invite_detail
    assert invite_keyboard.inline_keyboard[0][0].copy_text.text == (
        "https://t.me/lottery_bot?start=ref_AAAAAAAAAAAAAAAAAAAAAA"
    )
    assert "每10条有效消息增加1次抽奖机会" in activity_detail
    assert "每条至少" not in activity_detail and "文字、图片、表情均计数" in activity_detail
    assert "距下一次还差 2 条" in activity_detail
    _assert_callback_limit(overview_keyboard, invite_keyboard, activity_keyboard)


def _bonus_state() -> tuple[object, object, dict[int, object]]:
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("invite", "activity"),
        invite=InviteRule(retention_hours=1),
        group_invite_rules={"-1001": GroupInviteRule(invites_per_cycle=2)},
        group_activity_rules={
            "-1001": GroupActivityRule(messages_per_cycle=10, min_text_length=20)
        },
    )
    campaign = SimpleNamespace(
        id="campaign-1",
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        name="双加成活动",
        state=CampaignState.RUNNING,
        rules=rules.model_dump(mode="json"),
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=2),
        draw_at=now + timedelta(hours=3),
        timezone="Asia/Shanghai",
        presentation={"participation_groups": [{"id": -1001, "title": "A群"}]},
    )
    progress = SimpleNamespace(
        base_progress="",
        invitations=(SimpleNamespace(chat_id=-1001, valid_invites=3, awarded_tickets=1),),
        activities=(
            SimpleNamespace(
                chat_id=-1001,
                valid_messages=8,
                awarded_tickets=0,
                total_awarded_tickets=2,
                qualified_days=2,
            ),
        ),
    )
    route = SimpleNamespace(
        public_ref="AAAAAAAAAAAAAAAAAAAAAA",
        status="active",
        bonus_join_deadline=now + timedelta(hours=1),
    )
    link = SimpleNamespace(id="link-1", encrypted_url=b"secret", revoked_at=None)
    statuses = {-1001: GroupInviteLinkStatus(-1001, link, OutboxState.CONFIRMED, route)}
    return campaign, progress, statuses


def test_group_record_callbacks_fit_telegram_limit() -> None:
    records = CampaignInvitationRecords(
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        campaign_name="活动",
        timezone="Asia/Shanghai",
        draw_at=datetime.now(UTC),
        group_chat_id=-1001234567890,
        group_title="测试群",
        group_page=12,
        status_filter="invalid",
        pending=0,
        valid=0,
        invalid=11,
        page=0,
        total_pages=2,
        rows=(),
    )

    keyboard = _invitation_keyboard(records)
    callback_values = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert callback_values
    assert all(len(value.encode()) <= 64 for value in callback_values)


def test_activity_only_ticket_detail_has_no_invitation_entry() -> None:
    keyboard = _record_keyboard(
        "AbCdEfGhIjKlMnOpQrSt-_",
        bonus_enabled=True,
        invite_enabled=False,
    )
    labels = [plain_label(button.text) for row in keyboard.inline_keyboard for button in row]

    assert "我的加成" in labels
    assert "查看本期邀请记录" not in labels


async def test_drawn_campaign_hides_entries_but_keeps_invitation_history() -> None:
    campaign, progress, _ = _bonus_state()
    campaign.state = CampaignState.DRAWN
    campaign.cutoff_at = datetime.now(UTC) - timedelta(minutes=1)
    target = SimpleNamespace(
        kind="participation_group",
        chat_id=-1001,
        public_join_url=None,
        encrypted_join_url=b"private-join",
        revoked_at=None,
        secret_context="campaign-target",
    )
    queries = BonusQueries(progress, {}, targets=(target,))
    cipher = SimpleNamespace(
        decrypt=lambda encrypted, *, context: (
            "https://t.me/+normal-private" if context == "campaign-target" else ""
        )
    )

    detail, keyboard = await build_bonus_group_detail(
        campaign,
        user_id=201,
        category="invites",
        chat_id=-1001,
        return_page=0,
        queries=queries,
        cipher=cipher,
        bot_username="lottery_bot",
        unavailable={},
    )

    assert "邀请入口已隐藏" in detail
    assert "不再计算新增邀请加成" in detail
    assert "已获得 1 次" in detail
    assert "距下一次还差" not in detail
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert not any(button.url or button.copy_text for button in buttons)
    assert "邀请记录" in [plain_label(button.text) for button in buttons]

    activity_detail, _ = await build_bonus_group_detail(
        campaign,
        user_id=201,
        category="activity",
        chat_id=-1001,
        return_page=0,
        queries=queries,
        cipher=cipher,
        bot_username="lottery_bot",
        unavailable={},
    )
    assert "累计达标 2 天、获得 2 次" in activity_detail
    assert "不再计算新增活跃加成" in activity_detail
    assert "距下一次还差" not in activity_detail


@pytest.mark.parametrize(
    "state",
    [
        CampaignState.DRAWN,
        CampaignState.ARCHIVED,
        CampaignState.CANCELLED,
        CampaignState.READY_TO_DRAW,
        CampaignState.DRAWING,
        CampaignState.SETTLING,
        CampaignState.RUNNING,
    ],
)
async def test_closed_bonus_lists_and_details_keep_history_without_new_reward_prompt(state) -> None:
    campaign, progress, statuses = _bonus_state()
    campaign.state = state
    campaign.cutoff_at = datetime.now(UTC) - timedelta(minutes=1)
    common = _view_dependencies(progress, statuses)
    overview, overview_keyboard = await build_bonus_overview(campaign, **common)
    assert "不再计算新增加成" in overview
    assert "累计 2 次" in overview
    assert "今日" not in plain_label(overview_keyboard.inline_keyboard[0][1].text)
    assert "今日" not in overview
    for category in ("invites", "activity"):
        listing, _ = await build_bonus_category(campaign, category=category, page=0, **common)
        detail, keyboard = await build_bonus_group_detail(
            campaign, category=category, chat_id=-1001, return_page=0, **common
        )
        assert "距下一次还差" not in listing + detail
        assert (
            "获得 2 次" in listing + detail
            if category == "activity"
            else "已获得 1 次" in listing + detail
        )
        if state in {CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED}:
            assert not any(
                button.url or button.copy_text for row in keyboard.inline_keyboard for button in row
            )
    legacy, _ = await build_group_invite_payload(
        campaign, page=0, **{key: value for key, value in common.items() if key != "unavailable"}
    )
    assert "距下一次还差" not in legacy
    assert "累计达标" not in legacy
    assert "有效邀请 3 人" in legacy and "已获得 1 次" in legacy


async def test_join_deadline_preserves_awarded_invites_and_activity_progress() -> None:
    campaign, progress, statuses = _bonus_state()
    campaign.cutoff_at = datetime.now(UTC) + timedelta(minutes=30)
    common = _view_dependencies(progress, statuses)
    listing, _ = await build_bonus_category(campaign, category="invites", page=0, **common)
    detail, keyboard = await build_bonus_group_detail(
        campaign, category="invites", chat_id=-1001, return_page=0, **common
    )
    activity, _ = await build_bonus_group_detail(
        campaign, category="activity", chat_id=-1001, return_page=0, **common
    )
    assert "最晚计奖入群时间已过" in listing + detail
    assert "仅核验此前邀请" in detail
    assert "有效邀请 3 人" in detail and "已获得 1 次" in detail
    assert "距下一次还差" not in listing + detail
    assert plain_label(keyboard.inline_keyboard[0][0].text).startswith("复制入群链接")
    assert "不计加成" in plain_label(keyboard.inline_keyboard[0][0].text)
    assert "距下一次还差 2 条" in activity


async def test_scheduled_bonus_before_preheat_is_not_described_as_completed() -> None:
    campaign, progress, statuses = _bonus_state()
    campaign.state = CampaignState.SCHEDULED
    campaign.preheat_at = datetime.now(UTC) + timedelta(minutes=30)
    detail, _ = await build_bonus_group_detail(
        campaign,
        category="invites",
        chat_id=-1001,
        return_page=0,
        **_view_dependencies(progress, statuses),
    )
    assert "邀请加成尚未开始" in detail
    assert "活动已截止" not in detail
    assert "距下一次还差 1 人" in detail


@pytest.mark.parametrize("category", ["invites", "activity"])
async def test_single_bonus_category_keeps_closed_progress_and_group_return_route(category) -> None:
    campaign, progress, statuses = _bonus_state()
    rules = CampaignRules.model_validate(campaign.rules)
    disabled = "group_activity_rules" if category == "invites" else "group_invite_rules"
    campaign.rules = rules.model_dump(mode="json") | {disabled: {}}
    campaign.state = CampaignState.CANCELLED
    common = _view_dependencies(progress, statuses, outcome=CampaignOutcomeFacts("cancelled"))
    text, keyboard = await build_bonus_overview(campaign, **common)
    assert "本场已取消" in text
    assert "距下一次还差" not in text
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert any(plain_label(button.text).startswith("返回") for button in buttons)
    assert not any(plain_label(button.text) == "A群" for button in buttons)


async def test_no_enabled_group_bonus_reports_explicit_error() -> None:
    campaign, progress, statuses = _bonus_state()
    campaign.rules = CampaignRules().model_dump(mode="json")
    with pytest.raises(UserFacingError, match="没有可展示的逐群加成"):
        await build_bonus_overview(campaign, **_view_dependencies(progress, statuses))


async def test_cancelled_legacy_invite_entry_keeps_history_without_loading_revoked_link() -> None:
    campaign, _, _ = _bonus_state()
    campaign.state = CampaignState.CANCELLED
    text, keyboard = await legacy_invite_link_payload(
        campaign,
        user_id=201,
        queries=BonusQueries(None, {}, outcome=CampaignOutcomeFacts("cancelled")),
        cipher=object(),
    )
    assert "本场已取消" in text
    assert "历史邀请记录和已确认加成保留" in text
    assert not any(
        button.url or button.copy_text for row in keyboard.inline_keyboard for button in row
    )
    assert any(
        plain_label(button.text) == "邀请记录" for row in keyboard.inline_keyboard for button in row
    )


async def test_closed_legacy_bonus_keeps_awarded_history_after_bot_loses_group_permission() -> None:
    campaign, progress, statuses = _bonus_state()
    campaign.state = CampaignState.DRAWN
    common = _view_dependencies(progress, statuses) | {"unavailable": {-1001: "机器人已离群"}}
    text, _ = await build_group_invite_payload(campaign, page=0, **common)
    assert "有效邀请 3 人" in text and "已获得 1 次" in text
    activity, _ = await build_bonus_category(campaign, category="activity", page=0, **common)
    assert "累计达标 2 天、获得 2 次" in activity
    assert "机器人已离群" in text
    assert "累计达标" not in text
    assert "距下一次还差" not in text


def _view_dependencies(
    progress: object, statuses: dict[int, object], *, outcome: CampaignOutcomeFacts | None = None
) -> dict[str, object]:
    return {
        "user_id": 201,
        "queries": BonusQueries(progress, statuses, outcome=outcome),
        "cipher": SimpleNamespace(decrypt=lambda *_args, **_kwargs: "https://t.me/+raw-link"),
        "bot_username": "lottery_bot",
        "unavailable": {},
    }


def _assert_callback_limit(*keyboards: object) -> None:
    callback_values = [
        button.callback_data
        for keyboard in keyboards
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert all(len(value.encode()) <= 64 for value in callback_values)
