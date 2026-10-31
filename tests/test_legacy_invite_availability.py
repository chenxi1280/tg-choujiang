from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram import member_invites
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.ui_style import plain_label

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
PERSONAL_LINK = "https://t.me/+legacy_personal"


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("revoked", "个人邀请链接已撤销"),
        ("expired", "个人邀请链接已过期"),
        ("expires_now", "个人邀请链接已过期"),
        ("settling_expired", "个人邀请链接已过期"),
    ],
)
async def test_legacy_entry_reports_unavailable_link_without_exposing_it(
    monkeypatch, *, phase, expected
):
    monkeypatch.setattr(member_invites, "utcnow", lambda: NOW)
    campaign = _campaign()
    link = _link()
    if phase == "revoked":
        link.revoked_at = NOW - timedelta(minutes=1)
    elif phase == "expires_now":
        link.expire_at = NOW
    else:
        link.expire_at = NOW - timedelta(minutes=1)
    if phase == "settling_expired":
        campaign.state = CampaignState.SETTLING
        campaign.cutoff_at = link.expire_at
    message, flow, cipher = _flow(campaign, link)

    await flow.show_invite_link(message, campaign.public_ref, user_id=201)

    text = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert expected in text
    assert PERSONAL_LINK not in text
    assert "仍可入群" not in text
    assert not any(button.url or button.copy_text for button in buttons)
    assert any(plain_label(button.text) == "邀请记录" for button in buttons)
    cipher.decrypt.assert_not_called()


async def test_legacy_valid_old_link_remains_available_for_joining_after_bonus_cutoff(monkeypatch):
    monkeypatch.setattr(member_invites, "utcnow", lambda: NOW)
    campaign = _campaign()
    campaign.state = CampaignState.SETTLING
    campaign.cutoff_at = NOW - timedelta(minutes=1)
    link = _link()
    link.is_current = False
    link.expire_at = NOW + timedelta(hours=1)
    message, flow, cipher = _flow(campaign, link)

    await flow.show_invite_link(message, campaign.public_ref, user_id=201)

    text = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert "本期已截止" in text
    assert "不再计算本期或以后任何抽奖加成票" in text
    assert PERSONAL_LINK in text
    assert plain_label(keyboard.inline_keyboard[0][0].text) == "复制入群链接"
    assert keyboard.inline_keyboard[1][0].url == PERSONAL_LINK
    assert plain_label(keyboard.inline_keyboard[2][0].text) == "转发入群"
    cipher.decrypt.assert_called_once_with(b"encrypted", context=f"invite-link:{link.id}")


def _campaign():
    return SimpleNamespace(
        id=uuid4(),
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        name="本期抽奖",
        state=CampaignState.RUNNING,
        preheat_at=NOW - timedelta(hours=1),
        cutoff_at=NOW + timedelta(days=2),
        timezone="Asia/Shanghai",
        rules=CampaignRules(bonus_types=("invite",)).model_dump(mode="json"),
    )


def _link():
    return SimpleNamespace(
        id=uuid4(), encrypted_url=b"encrypted", revoked_at=None, expire_at=None, is_current=True
    )


def _flow(campaign, link):
    queries = SimpleNamespace(
        campaign_by_reference=AsyncMock(return_value=campaign),
        invite_link_for_participant=AsyncMock(return_value=link),
    )
    cipher = Mock()
    cipher.decrypt.return_value = PERSONAL_LINK
    flow = MemberFlow(bot=object(), participants=object(), queries=queries, cipher=cipher)
    return SimpleNamespace(answer=AsyncMock()), flow, cipher
