from types import SimpleNamespace

from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.rules import CampaignRules, GroupActivityRule, GroupInviteRule
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_group_availability import GroupAvailability
from tg_lottery.telegram.creator_renderer import _group_button_text
from tg_lottery.telegram.gateway import _campaign_card_text
from tg_lottery.telegram.member_condition_views import condition_instruction, human_rule
from tg_lottery.telegram.member_popups import popup_failure

CHATS = (
    {"id": -1001, "title": "读书群", "type": "supergroup"},
    {"id": -1002, "title": "交流群", "type": "supergroup"},
    {"id": -1003, "title": "公告频道", "type": "channel"},
)


def _rules() -> CampaignRules:
    return CampaignRules(
        bonus_types=("invite", "activity"),
        message_thresholds={"-1001": 50},
        group_invite_rules={
            "-1001": GroupInviteRule(invites_per_cycle=3),
            "-1002": GroupInviteRule(invites_per_cycle=5, cap_mode="limited", max_bonus_tickets=7),
        },
        group_activity_rules={
            "-1002": GroupActivityRule(messages_per_cycle=20, min_text_length=8, reward_tickets=2),
        },
    )


def test_card_and_private_rules_render_each_frozen_group_bonus() -> None:
    rules = _rules()
    payload = {
        "name": "周末阅读",
        "timezone": "Asia/Shanghai",
        "draw_at": "2026-09-06T12:00:00+00:00",
        "prizes": [],
        "presentation": {"participation_groups": CHATS[:2], "required_chats": CHATS[2:]},
        "rules": rules.model_dump(mode="json"),
    }
    card = _campaign_card_text(payload)
    private = human_rule(rules, CHATS)
    for text in (card, private):
        assert "每邀请 3 个有效新成员 +1 次" in text
        assert "每邀请 5 个有效新成员 +1 次" in text
        assert "上限 7 次" in text
        assert "每日每满 20 条有效消息 +2 次" in text
        assert "每条至少 8 字" in text
        assert "-100" not in text
    assert "关注-公告频道" in card
    assert "累计发言至少 <b>50</b> 条" in card
    assert "全部达标" not in private


def test_optional_activity_bonus_does_not_become_registration_gate() -> None:
    text = condition_instruction(CHATS[1], _rules())
    assert "加入群聊「交流群」" in text
    assert "每天需" not in text
    assert "20" not in text
    assert "报名需在本群累计发言至少 50 条" in condition_instruction(CHATS[0], _rules())
    assert "发言" not in condition_instruction(CHATS[2], _rules())


def test_multi_group_failure_names_the_actual_missing_group() -> None:
    campaign = SimpleNamespace(presentation={"participation_groups": CHATS[:2]})
    observation = {"chat_id": -1002, "state": "ineligible"}
    text = popup_failure(campaign, QualificationState.INELIGIBLE, (observation,))
    assert "交流群" in text
    assert "条件群聊" not in text


def test_group_picker_shows_name_without_raw_chat_id() -> None:
    group = SimpleNamespace(title="读书群", chat_id=-100123456789)
    availability = GroupAvailability(True, "可选择", "")
    text = _group_button_text(group, availability, True, parallel_count=2)
    assert "读书群" in text and "另有 2 个" in text
    assert str(group.chat_id) not in text


def test_missing_title_is_explicit_without_exposing_numeric_identity() -> None:
    assert chat_title({"id": -1001, "title": "-1001"}).startswith("群聊")
    assert chat_title({"id": -1001, "username": "bookclub"}) == "@bookclub"


def test_publication_failure_has_named_actionable_explanation() -> None:
    from tg_lottery.application.publication_queries import PublicationIssue
    from tg_lottery.domain.enums import OutboxState
    from tg_lottery.telegram.admin_flow import _issues_text

    issue = PublicationIssue(
        -100123, "读书群", OutboxState.PRE_ACCEPT_FAILED, "Bad Request: chat not found"
    )
    text = _issues_text("发布未完成", (issue,))
    assert "读书群" in text and "机器人无法找到该群" in text
    assert "-100123" not in text and "Bad Request" not in text
    unknown = PublicationIssue(-100124, "-100124", OutboxState.UNKNOWN_HOLD, "unexpected failure")
    text = _issues_text("发布未完成", (unknown,))
    assert "远端结果未知" in text and "完整错误已记录" in text
    assert "-100124" not in text


def test_activity_list_uses_readable_positions_and_keeps_callback_identity() -> None:
    from datetime import UTC, datetime

    from tg_lottery.application.participant_records import (
        ParticipatedCampaign,
        ParticipatedCampaignPage,
    )
    from tg_lottery.domain.enums import CampaignState
    from tg_lottery.telegram.participant_list_views import build_participated_campaign_list

    references = ("AbCdEfGhIjKlMnOpQrSt-_", "GhIjKlMnOpQrStAbCdEf-_")
    rows = tuple(
        ParticipatedCampaign(
            reference,
            "同名活动",
            CampaignState.RUNNING,
            QualificationState.ELIGIBLE,
            1,
            datetime(2026, 9, 6, 8, tzinfo=UTC),
            "Asia/Shanghai",
        )
        for reference in references
    )
    page = ParticipatedCampaignPage("active", 0, 1, rows)
    text, markup = build_participated_campaign_list(page, invitation_only=False)
    assert "1. 同名活动" in text and "2. 同名活动" in text
    labels = " ".join(button.text for row in markup.inline_keyboard for button in row)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    for reference in references:
        assert reference[:6] not in text + labels
        assert f"nav:joined:{reference}" in callbacks
