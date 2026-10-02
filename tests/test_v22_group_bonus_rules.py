from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from tests.helpers import seed_campaign
from tests.v22_bonus_test_helpers import draft_data as _draft_data
from tests.v22_bonus_test_helpers import picker_query as _picker_query
from tg_lottery.application.activity_rules import (
    activity_min_text_length,
    group_activity_ticket_target,
)
from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.application.invitation_rewards import group_invite_bonus_target
from tg_lottery.application.query_invites import GroupInviteLinkStatus
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import (
    CampaignDraft,
    CampaignRules,
    GroupActivityRule,
    GroupInviteRule,
    InviteRule,
)
from tg_lottery.infrastructure.db import (
    Group,
    InviteLink,
)
from tg_lottery.telegram import creator_bonus
from tg_lottery.telegram.bot_permission_reminders import send_bot_permission_reminder
from tg_lottery.telegram.creator_group_availability import inspect_group_availability
from tg_lottery.telegram.creator_group_bonus import receive_input
from tg_lottery.telegram.creator_group_picker import CreatorGroupPicker
from tg_lottery.telegram.creator_group_selection import toggle_group
from tg_lottery.telegram.creator_publication import CreatorPublisher, confirmation_view
from tg_lottery.telegram.member_group_invites import (
    _group_rows,
    _missing_link_row,
    build_group_invite_payload,
)


class PublicationReviewBot:
    id = 9001

    async def get_chat(self, chat_id: int) -> object:
        return SimpleNamespace(
            id=chat_id,
            title=f"群 {chat_id}",
            full_name=None,
            type="supergroup",
            username="public_group",
            permissions=SimpleNamespace(can_send_messages=True),
        )

    async def get_chat_member(self, chat_id: int, user_id: int) -> object:
        if user_id != self.id:
            return SimpleNamespace(status="administrator", can_invite_users=True)
        if chat_id == -1001:
            return SimpleNamespace(status="member", can_invite_users=False)
        return SimpleNamespace(status="administrator", can_invite_users=False)


class PickerSessions:
    def __init__(self, data: dict[str, object]) -> None:
        self.state = "draft:idle"
        self.data = data

    async def get(self, user_id: int) -> object:
        return SimpleNamespace(state=self.state, data=self.data)

    async def set(self, user_id: int, state: str, data: dict[str, object]) -> None:
        self.state = state
        self.data = data


class BonusProgressQueries:
    def __init__(self, progress: object) -> None:
        self._progress = progress

    async def group_bonus_progress(self, campaign_id, user_id):
        return self._progress

    async def group_invite_link_statuses(self, campaign_id, user_id):
        return {}


def test_group_invite_bonus_supports_independent_limited_and_unlimited_targets() -> None:
    unlimited = GroupInviteRule(invites_per_cycle=2)
    limited = GroupInviteRule(
        invites_per_cycle=3,
        cap_mode="limited",
        max_bonus_tickets=5,
    )

    assert group_invite_bonus_target(unlimited, 20) == 10
    assert group_invite_bonus_target(limited, 15) == 5
    assert group_invite_bonus_target(limited, 18) == 5


def test_legacy_ratio_invite_bonus_can_be_explicitly_uncapped() -> None:
    rule = InviteRule(
        mode="ratio",
        valid_invites_per_ticket=2,
        max_bonus_tickets=None,
    )

    assert rule.max_bonus_tickets is None


def test_group_activity_targets_and_minimum_lengths_are_independent() -> None:
    rules = CampaignRules(
        bonus_types=("activity",),
        group_activity_rules={
            "-1001": GroupActivityRule(
                messages_per_cycle=10,
                min_text_length=20,
                daily_cap_mode="limited",
                daily_max_cycles=1,
            ),
            "-1002": GroupActivityRule(
                messages_per_cycle=20,
                min_text_length=5,
                daily_cap_mode="unlimited",
                daily_max_cycles=None,
            ),
        },
    )
    counts = {"-1001": 50, "-1002": 440}

    assert group_activity_ticket_target(counts, -1001, rules) == 1
    assert group_activity_ticket_target(counts, -1002, rules) == 22
    assert activity_min_text_length(rules, -1001) == 20
    assert activity_min_text_length(rules, -1002) == 5


@pytest.mark.parametrize(
    ("state", "label", "actionable"),
    [
        (None, "获取 A群 链接", True),
        (OutboxState.PENDING, "🔄 创建中 · A群", True),
        (OutboxState.PRE_ACCEPT_FAILED, "重试 A群 链接", True),
        (OutboxState.UNKNOWN_HOLD, "🔄 核验 A群 链接状态", True),
    ],
)
def test_group_invite_link_buttons_follow_delivery_certainty(
    state, label: str, actionable: bool
) -> None:
    now = datetime.now(UTC)
    campaign = SimpleNamespace(
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        state=CampaignState.RUNNING,
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=1),
    )
    status = GroupInviteLinkStatus(-1001, None, state) if state is not None else None

    button = _missing_link_row(campaign, {"id": -1001, "title": "A群"}, status)[0][0]

    assert button.text == label
    assert (button.callback_data != "member:noop") is actionable


def test_active_group_invite_link_has_copy_and_share_actions() -> None:
    now = datetime.now(UTC)
    campaign = SimpleNamespace(
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        state=CampaignState.RUNNING,
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=1),
    )
    link = SimpleNamespace(id="link-1", encrypted_url=b"secret", revoked_at=None)
    status = GroupInviteLinkStatus(-1001, link, OutboxState.CONFIRMED)
    cipher = SimpleNamespace(decrypt=lambda encrypted, *, context: "https://t.me/+active")

    buttons = _group_rows(campaign, {"id": -1001, "title": "A群"}, status, cipher=cipher)[0]

    assert buttons[0].copy_text.text == "https://t.me/+active"
    assert "share/url" in buttons[1].url


@pytest.mark.parametrize(
    "state", [CampaignState.RUNNING, CampaignState.DRAWN, CampaignState.CANCELLED]
)
async def test_activity_only_bonus_progress_is_paginated_by_six_groups(state) -> None:
    now = datetime.now(UTC)
    chat_ids = tuple(-1000 - index for index in range(7))
    rules = CampaignRules(
        bonus_types=("activity",),
        group_activity_rules={str(chat_id): GroupActivityRule() for chat_id in chat_ids},
    )
    campaign = SimpleNamespace(
        id="campaign-1",
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        name="七群活动",
        state=state,
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=1),
        rules=rules.model_dump(mode="json"),
        presentation={
            "participation_groups": [
                {"id": chat_id, "title": f"群{index}"} for index, chat_id in enumerate(chat_ids)
            ]
        },
    )
    progress = SimpleNamespace(
        invitations=(),
        activities=tuple(
            SimpleNamespace(
                chat_id=chat_id,
                valid_messages=index,
                awarded_tickets=0,
                total_awarded_tickets=index,
                qualified_days=index,
            )
            for index, chat_id in enumerate(chat_ids)
        ),
    )
    queries = BonusProgressQueries(progress)

    first, _ = await build_group_invite_payload(
        campaign, user_id=201, page=0, queries=queries, cipher=object()
    )
    second, _ = await build_group_invite_payload(
        campaign, user_id=201, page=1, queries=queries, cipher=object()
    )

    assert "群0" in first and "群5" in first and "群6" not in first
    assert "群6" in second and "群0" not in second
    if state is not CampaignState.RUNNING:
        assert "距下一次还差" not in first + second
        assert "累计达标 6 天、获得 6 次" in second


def test_invite_join_deadline_keeps_legacy_group_link_without_bonus_share_prompt() -> None:
    now = datetime.now(UTC)
    campaign = SimpleNamespace(
        state=CampaignState.RUNNING,
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(hours=1),
    )
    link = SimpleNamespace(id="link-1", encrypted_url=b"secret", revoked_at=None)
    status = GroupInviteLinkStatus(-1001, link, OutboxState.CONFIRMED)
    cipher = SimpleNamespace(decrypt=lambda encrypted, *, context: "https://t.me/+join-only")
    rows = _group_rows(
        campaign,
        {"id": -1001, "title": "A群"},
        status,
        cipher=cipher,
        bonus_deadline=now - timedelta(minutes=1),
    )
    assert len(rows) == 1 and len(rows[0]) == 1
    assert rows[0][0].url == "https://t.me/+join-only"
    assert "不计加成" in rows[0][0].text
    assert rows[0][0].copy_text is None


def test_unlimited_rules_reject_a_fake_numeric_cap() -> None:
    with pytest.raises(ValidationError, match="cannot store a cap"):
        GroupInviteRule(cap_mode="unlimited", max_bonus_tickets=999999)
    with pytest.raises(ValidationError, match="cannot store a cap"):
        GroupActivityRule(daily_cap_mode="unlimited", daily_max_cycles=999999)


async def test_legacy_link_uniqueness_survives_nullable_group_migration(
    session_factory,
) -> None:
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            _, campaign, participant = await seed_campaign(session)
            session.add_all(
                (
                    InviteLink(
                        campaign_id=campaign.id,
                        participant_id=participant.id,
                        link_hash="c" * 64,
                        telegram_name="lottery_legacy_one",
                        expire_at=campaign.cutoff_at,
                    ),
                    InviteLink(
                        campaign_id=campaign.id,
                        participant_id=participant.id,
                        link_hash="d" * 64,
                        telegram_name="lottery_legacy_two",
                        expire_at=campaign.cutoff_at,
                    ),
                )
            )


def test_draft_rejects_bonus_rules_for_a_non_participating_group() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="require participation groups"):
        CampaignDraft(
            group_id=-1001,
            participation_group_ids=(-1001,),
            name="逐群加成",
            preheat_at=now,
            cutoff_at=now + timedelta(hours=1),
            draw_at=now + timedelta(hours=2),
            rules=CampaignRules(
                bonus_types=("invite",),
                group_invite_rules={"-1002": GroupInviteRule()},
            ),
        )


def test_creator_group_editor_is_atomic_and_accepts_custom_positive_input() -> None:
    data = _draft_data()
    opened = creator_bonus.apply_bonus_action(data, "draft_bonus_invite_group:-1001")
    changed = creator_bonus.apply_bonus_action(opened, "draft_bonus_invite_cycle_inc")
    awaiting = creator_bonus.apply_bonus_action(changed, "draft_bonus_input_cycle")
    entered = receive_input(awaiting, "7")

    assert data["group_invite_rules"] == {}
    assert entered["bonus_working_copy"]["rule"]["invites_per_cycle"] == 7
    saved = creator_bonus.apply_bonus_action(entered, "draft_bonus_invite_save")
    assert saved["group_invite_rules"]["-1001"]["invites_per_cycle"] == 7
    assert saved["group_invite_rules"]["-1001"]["cap_mode"] == "unlimited"


def test_saved_group_rule_can_be_copied_only_after_explicit_confirmation() -> None:
    source = GroupInviteRule(invites_per_cycle=3).model_dump(mode="json")
    data = _draft_data() | {"group_invite_rules": {"-1001": source}}
    opened = creator_bonus.apply_bonus_action(data, "draft_bonus_invite_group:-1001")
    selecting = creator_bonus.apply_bonus_action(opened, "draft_bonus_copy_start")
    selected = creator_bonus.apply_bonus_action(selecting, "draft_bonus_copy_toggle:-1002")
    awaiting = creator_bonus.apply_bonus_action(selected, "draft_bonus_copy_continue")

    assert "-1002" not in awaiting["group_invite_rules"]
    copied = creator_bonus.apply_bonus_action(awaiting, "draft_bonus_copy_confirm")
    assert copied["group_invite_rules"]["-1002"] == source
    assert copied["group_invite_rules"]["-1002"] is not source


def test_unsaved_group_rule_cannot_be_copied() -> None:
    source = GroupInviteRule().model_dump(mode="json")
    data = _draft_data() | {"group_invite_rules": {"-1001": source}}
    opened = creator_bonus.apply_bonus_action(data, "draft_bonus_invite_group:-1001")
    changed = creator_bonus.apply_bonus_action(opened, "draft_bonus_invite_cycle_inc")

    with pytest.raises(UserFacingError, match="先保存"):
        creator_bonus.apply_bonus_action(changed, "draft_bonus_copy_start")


def test_disabling_group_rule_retains_parameters_for_reenable() -> None:
    source = GroupInviteRule(invites_per_cycle=7).model_dump(mode="json")
    data = _draft_data() | {
        "bonus_types": ["invite"],
        "group_invite_rules": {"-1001": source},
    }
    opened = creator_bonus.apply_bonus_action(data, "draft_bonus_invite_group:-1001")
    disabled = creator_bonus.apply_bonus_action(opened, "draft_bonus_group_disable")

    assert disabled["group_invite_rules"]["-1001"]["enabled"] is False
    assert disabled["group_invite_rules"]["-1001"]["invites_per_cycle"] == 7
    assert disabled["bonus_types"] == []
    reopened = creator_bonus.apply_bonus_action(
        disabled | {"bonus_types": ["invite"]}, "draft_bonus_invite_group:-1001"
    )
    assert reopened["bonus_working_copy"]["rule"]["enabled"] is True
    assert reopened["bonus_working_copy"]["rule"]["invites_per_cycle"] == 7


def test_publication_confirmation_summarizes_all_group_rules() -> None:
    data = _draft_data() | {
        "group_invite_rules": {
            "-1001": GroupInviteRule().model_dump(mode="json"),
            "-1002": GroupInviteRule(cap_mode="limited", max_bonus_tickets=5).model_dump(
                mode="json"
            ),
        },
        "group_activity_rules": {
            "-1001": GroupActivityRule(
                daily_cap_mode="unlimited", daily_max_cycles=None
            ).model_dump(mode="json")
        },
    }

    text, rows = confirmation_view(data)

    assert "参与群" in text and "2 个" in text
    assert "邀请加成" in text and "2 个群开启" in text
    assert "发言加成" in text and "1 个每日不设上限" in text
    assert [(label, action) for row in rows for label, action in row][-2] == (
        "确认发布",
        "draft_publish_confirm",
    )


async def test_group_selector_explains_when_bot_is_not_an_admin() -> None:
    bot = SimpleNamespace(
        id=9001,
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status="member"),
                SimpleNamespace(status="administrator"),
            ]
        ),
    )

    result = await inspect_group_availability(bot, -1001, 201)

    assert result.selectable is False
    assert result.status == "机器人不是管理员"
    assert "提升为管理员" in result.reason


async def test_bot_join_without_admin_rights_sends_an_actionable_reminder() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        chat=SimpleNamespace(id=-1001, type="group"),
        old_chat_member=SimpleNamespace(status="left"),
        new_chat_member=SimpleNamespace(status="member"),
    )

    await send_bot_permission_reminder(bot, event)

    text = bot.send_message.await_args.args[1]
    assert "设为管理员" in text
    assert "重新检测" in text


async def test_publication_review_reports_all_group_permission_failures() -> None:
    bot = PublicationReviewBot()
    publisher = CreatorPublisher(
        bot=bot,
        bot_username="test_bot",
        sessions=object(),
        campaigns=object(),
        queries=object(),
        profiles=object(),
        renderer=object(),
    )
    data = _draft_data() | {
        "bonus_types": ["invite"],
        "group_invite_rules": {"-1002": GroupInviteRule().model_dump(mode="json")},
    }

    with pytest.raises(UserFacingError) as captured:
        await publisher._review_targets(data, 201)

    message = captured.value.public_message
    assert "-1001" in message and "设为管理员" in message
    assert "B群" in message and "邀请" in message


async def test_removing_configured_group_requires_confirmation() -> None:
    source = GroupInviteRule().model_dump(mode="json")
    data = _draft_data() | {"group_invite_rules": {"-1001": source}}
    sessions = PickerSessions(data)
    group = Group(chat_id=-1001, title="A群", timezone="Asia/Shanghai")
    queries = SimpleNamespace(group_by_chat_id=AsyncMock(return_value=group))
    renderer = SimpleNamespace(render=AsyncMock(), render_groups=AsyncMock())
    picker = CreatorGroupPicker(
        bot=SimpleNamespace(),
        sessions=sessions,
        queries=queries,
        renderer=renderer,
    )
    query = _picker_query()

    await picker.toggle(query, CallbackGrant("draft_toggle_group", None, {"chat_id": -1001}))

    assert sessions.data["participation_group_ids"] == [-1001, -1002]
    assert sessions.data["pending_group_removal_ids"] == [-1001]
    assert "确认后将删除" in renderer.render.await_args.kwargs["text"]
    await picker.confirm_remove(query, CallbackGrant("draft_group_remove_confirm", None, {}))
    assert sessions.data["participation_group_ids"] == [-1002]
    assert sessions.data["group_invite_rules"] == {}


def test_removing_a_group_removes_only_its_bonus_rules() -> None:
    data = _draft_data() | {
        "group_invite_rules": {
            "-1001": GroupInviteRule().model_dump(mode="json"),
            "-1002": GroupInviteRule(invites_per_cycle=3).model_dump(mode="json"),
        }
    }
    group = type("GroupRow", (), {"chat_id": -1001, "title": "A群", "timezone": "UTC"})()

    updated = toggle_group(data, group)

    assert updated["participation_group_ids"] == [-1002]
    assert set(updated["group_invite_rules"]) == {"-1002"}
