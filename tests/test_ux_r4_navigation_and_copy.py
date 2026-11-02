from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_lottery.application.callback_tokens import CallbackGrant
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.presentation.counting_notice import blocked_counting_notice
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_behavior_views import return_target
from tg_lottery.telegram.creator_sections import back_rows, section_view
from tg_lottery.telegram.draft_config import new_draft
from tg_lottery.telegram.group_reputation import COMMANDS, _reply
from tg_lottery.telegram.home_views import creation_entry
from tg_lottery.telegram.member_popups import popup_already_participated, popup_success
from tg_lottery.telegram.member_record_text import _ticket_summary, current_effective_count


@pytest.mark.parametrize("source,expected", [("dashboard", "草稿"), ("conditions", "参与条件")])
def test_gate_return_label_tracks_actual_entry(source, expected):
    assert return_target(new_draft() | {"creator_path": ["dashboard", source]}) == expected


@pytest.mark.parametrize(
    "state,parent,expected",
    [
        ("draft:idle", ["dashboard", "conditions", "user_limits"], "conditions"),
        ("draft:regions", ["dashboard", "conditions", "user_limits"], "user_limits"),
        ("draft:start", ["dashboard", "more"], "more"),
        ("draft:participation_delete", ["dashboard", "conditions", "requirements"], "requirements"),
    ],
)
async def test_return_callback_matches_page_and_field_label(state, parent, expected):
    data = new_draft() | {"creator_path": parent}
    creator = object.__new__(CampaignCreator)
    creator._sessions = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(state=state, data=data))
    )
    creator._show_section = AsyncMock()
    creator._render_dashboard = AsyncMock()
    if state == "draft:idle":
        _, rows = section_view(data, parent[-1])
        label = rows[-1][0][0]
    else:
        label = back_rows(data, input_field=True)[0][0][0]
    query = SimpleNamespace(from_user=SimpleNamespace(id=201))
    await creator._show_dashboard(query, CallbackGrant("draft_back", None, {}))
    assert creator._show_section.await_args.args[1] == expected
    assert label.startswith("↩️ 返回")
    assert creator._show_section.await_args.kwargs["data"]["creator_path"][-1] == expected


@pytest.mark.parametrize("registered,expected", [(False, "报名尚未成功"), (True, "你已报名")])
def test_blocked_counting_copy_preserves_window_conditions_and_no_backfill(registered, expected):
    campaign = SimpleNamespace(
        name="测试活动", cutoff_at=datetime(2026, 11, 5, 12, tzinfo=UTC), timezone="Asia/Shanghai"
    )
    text = blocked_counting_notice(campaign, registered=registered)
    assert expected in text and "本条发言不计数" in text
    assert "不补算" in text and "条件" in text
    assert "2026年11月5日 20:00" in text
    if registered:
        assert "完成门槛并满足参与条件后生效" in text
        assert "当前有效抽奖次数为 0" in text
    assert "暂存加成次数" not in text


@pytest.mark.parametrize(
    "alias,canonical",
    [("人品", "发言加成票"), ("人品排行", "发言加成票排行"), ("人品规则", "发言加成票规则")],
)
async def test_speech_bonus_query_aliases_and_reply_heading(alias, canonical):
    assert COMMANDS[alias] == COMMANDS[canonical]
    message = SimpleNamespace(answer=AsyncMock())
    await _reply(message, "查询结果", rows=[], edit=False)
    assert message.answer.await_args.args[0] == "人品\uff1d本场发言加成票\n查询结果"


def test_registration_and_creation_use_canonical_user_terms():
    assert creation_entry(None) == ("创建抽奖", "nav:create")
    assert popup_success(4) == "✅ 报名成功\uff01有效抽奖次数\uff1a4"
    assert "你已报名" in popup_already_participated(4)


def test_total_keeps_both_bonus_sources_while_effective_count_obeys_gate():
    detail = SimpleNamespace(
        state=CampaignState.RUNNING,
        qualification=QualificationState.ELIGIBLE,
        total=4,
        registration=1,
        activity=1,
        invitation=2,
        base_qualified=False,
        membership_progress="",
        base_progress="门槛尚未完成",
    )
    assert "总抽奖次数\uff1a4" in _ticket_summary(detail)
    assert current_effective_count(detail) == "0"
    detail.base_qualified = True
    assert current_effective_count(detail) == "4"
    assert (detail.registration, detail.activity, detail.invitation) == (1, 1, 2)
