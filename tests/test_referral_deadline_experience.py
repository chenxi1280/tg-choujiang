from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.domain.rules import CampaignRules, InviteRule
from tg_lottery.telegram.member_group_invites import _group_rows, _invite_link_note
from tg_lottery.telegram.member_referral_landing import (
    _bonus_active,
    _group_url,
    _landing_keyboard,
    _landing_text,
)
from tg_lottery.telegram.ui_style import plain_label

NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)


@pytest.fixture(autouse=True)
def freeze_clock(monkeypatch):
    monkeypatch.setattr("tg_lottery.telegram.member_referral_landing.utcnow", lambda: NOW)
    monkeypatch.setattr("tg_lottery.telegram.member_group_invites.utcnow", lambda: NOW)


def _landing(state=CampaignState.RUNNING):
    campaign = SimpleNamespace(
        name="阅读抽奖",
        rules=CampaignRules(invite=InviteRule(retention_hours=1)).model_dump(mode="json"),
        public_ref="ABCDEF123456",
        state=state,
        timezone="Asia/Shanghai",
        preheat_at=NOW - timedelta(days=1),
        cutoff_at=NOW + timedelta(hours=2),
        draw_at=NOW + timedelta(hours=3),
    )
    return SimpleNamespace(
        campaign=campaign,
        route=SimpleNamespace(status="active", bonus_join_deadline=NOW + timedelta(hours=1)),
        target=SimpleNamespace(title_snapshot="读书群", public_join_url="https://t.me/book_group"),
        link=SimpleNamespace(id="test", encrypted_url="encrypted", revoked_at=None, expire_at=None),
    )


def test_referral_landing_explains_dates_unknown_membership_and_campaign_scope() -> None:
    text = _landing_text(_landing(), None, now=NOW)
    assert "2026年09月05日 17:00 (北京时间)" in text
    assert "2026年09月05日 19:00 (北京时间)" in text
    assert "暂时无法核验" in text
    assert "不计入下次抽奖" in text


@pytest.mark.parametrize(
    "state", [CampaignState.SETTLING, CampaignState.DRAWN, CampaignState.ARCHIVED]
)
def test_ended_referral_keeps_joining_without_registration_button(state) -> None:
    landing = _landing(state)
    flow = SimpleNamespace(_cipher=Mock(), _bot_username="lottery_bot")
    flow._cipher.decrypt.return_value = "https://t.me/+persistent"
    markup = _landing_keyboard(flow, landing, False)
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert buttons[0].url == "https://t.me/+persistent"
    assert "报名本次抽奖" not in [plain_label(button.text) for button in buttons]
    assert buttons[-1].callback_data == "nav:join"


def test_old_expired_invite_is_not_presented_as_usable() -> None:
    landing = _landing(CampaignState.DRAWN)
    landing.link.expire_at = NOW - timedelta(days=1)
    landing.target.public_join_url = None
    flow = SimpleNamespace(_cipher=Mock())
    assert _group_url(flow, landing) is None
    flow._cipher.decrypt.assert_not_called()
    status = SimpleNamespace(link=landing.link, delivery_state=OutboxState.CONFIRMED)
    assert "邀请入口已隐藏" in _invite_link_note(landing.campaign, status)
    assert "旧版邀请链接已过期" in _invite_link_note(landing.campaign, status)
    assert "仍有效的旧链接可入群" not in _invite_link_note(landing.campaign, status)
    rows = _group_rows(
        landing.campaign, {"title": "读书群", "id": -1001}, status, cipher=flow._cipher
    )
    assert rows == []


def test_early_cutoff_shortens_displayed_invitation_deadline() -> None:
    landing = _landing(CampaignState.SETTLING)
    landing.campaign.cutoff_at = NOW + timedelta(minutes=30)
    text = _landing_text(landing, False, now=NOW)
    assert "2026年09月05日 15:30 (北京时间)" in text
    assert "17:00" not in text
    assert "本期邀请加成票已经结束" in text


@pytest.mark.parametrize(("offset_seconds", "active"), [(-1, False), (0, True), (1, True)])
def test_referral_bonus_starts_at_preheat_boundary(offset_seconds, active) -> None:
    landing = _landing(CampaignState.SCHEDULED)
    landing.campaign.preheat_at = NOW
    observed_at = NOW + timedelta(seconds=offset_seconds)
    text = _landing_text(landing, False, now=observed_at)
    assert _bonus_active(landing, observed_at) is active
    if active:
        assert "完成留存后可计入邀请人的本期加成" in text
    else:
        assert "邀请加成票尚未开始" in text
        assert "当前入群不会给邀请人增加本期抽奖次数" in text
        assert "已经结束" not in text


@pytest.mark.parametrize(("offset_seconds", "active"), [(-1, True), (0, True), (1, False)])
def test_referral_bonus_stops_after_effective_join_deadline(offset_seconds, active) -> None:
    landing = _landing()
    observed_at = landing.route.bonus_join_deadline + timedelta(seconds=offset_seconds)
    assert _bonus_active(landing, observed_at) is active
    text = _landing_text(landing, False, now=observed_at)
    assert ("完成留存后可计入邀请人的本期加成" in text) is active


def test_referral_before_preheat_keeps_ordinary_join_and_registration_options() -> None:
    landing = _landing(CampaignState.SCHEDULED)
    landing.campaign.preheat_at = NOW + timedelta(hours=1)
    flow = SimpleNamespace(_cipher=Mock(), _bot_username="lottery_bot")
    flow._cipher.decrypt.return_value = "https://t.me/+persistent"
    text = _landing_text(landing, None, now=NOW)
    keyboard = _landing_keyboard(flow, landing, False)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert "邀请加成票尚未开始" in text
    assert "暂时无法核验" in text
    assert buttons[0].url == "https://t.me/+persistent"
    assert "报名本次抽奖" in [plain_label(button.text) for button in buttons]


def test_ended_invite_note_preserves_confirmed_revocation_fact() -> None:
    landing = _landing(CampaignState.DRAWN)
    landing.link.revoked_at = NOW - timedelta(minutes=1)
    status = SimpleNamespace(link=landing.link, delivery_state=OutboxState.CONFIRMED)
    note = _invite_link_note(landing.campaign, status)
    assert "邀请入口已隐藏" in note
    assert "个人链接已撤销" in note
    assert "仍有效的旧链接可入群" not in note
