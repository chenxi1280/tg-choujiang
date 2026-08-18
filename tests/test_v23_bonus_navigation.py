from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tg_lottery.application.participant_records import CampaignInvitationRecords
from tg_lottery.application.query_invites import GroupInviteLinkStatus
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.rules import CampaignRules, GroupActivityRule, GroupInviteRule
from tg_lottery.telegram.member_bonus_views import (
    build_bonus_group_detail,
    build_bonus_overview,
)
from tg_lottery.telegram.member_records import _invitation_keyboard, _record_keyboard


class BonusQueries:
    def __init__(
        self,
        progress: object,
        statuses: dict[int, object],
        *,
        targets: tuple[object, ...] = (),
    ) -> None:
        self._progress = progress
        self._statuses = statuses
        self._targets = targets

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

    callbacks = [row[0].callback_data for row in overview_keyboard.inline_keyboard]
    assert "双加成活动 #AbCdEf" in overview
    assert callbacks[:2] == [
        "member:bonus_invites:AbCdEfGhIjKlMnOpQrSt-_:0",
        "member:bonus_activity:AbCdEfGhIjKlMnOpQrSt-_:0",
    ]
    assert "每 2 名有效新成员 +1" in invite_detail
    assert "不设上限" in invite_detail
    assert invite_keyboard.inline_keyboard[0][0].copy_text.text == (
        "https://t.me/lottery_bot?start=ref_AAAAAAAAAAAAAAAAAAAAAA"
    )
    assert "每 10 条有效消息 +1 次" in activity_detail
    assert "每条至少 20 个可计数字符" in activity_detail
    _assert_callback_limit(overview_keyboard, invite_keyboard, activity_keyboard)


def _bonus_state() -> tuple[object, object, dict[int, object]]:
    now = datetime.now(UTC)
    rules = CampaignRules(
        bonus_types=("invite", "activity"),
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
        invitations=(SimpleNamespace(chat_id=-1001, valid_invites=3, awarded_tickets=1),),
        activities=(SimpleNamespace(chat_id=-1001, valid_messages=8, awarded_tickets=0),),
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
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "我的加成" in labels
    assert "查看本期邀请记录" not in labels


async def test_cutoff_without_personal_link_uses_normal_private_group_entry() -> None:
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

    assert "活动已截止" in detail
    assert "不再计算新增邀请加成" in detail
    assert keyboard.inline_keyboard[0][0].url == "https://t.me/+normal-private"


def _assert_callback_limit(*keyboards: object) -> None:
    callback_values = [
        button.callback_data
        for keyboard in keyboards
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert all(len(value.encode()) <= 64 for value in callback_values)
