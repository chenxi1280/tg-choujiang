from types import SimpleNamespace

from tests.test_creator_leaf_experience import _message
from tests.test_creator_settings import RecordingRenderer, ThresholdSessions
from tg_lottery.domain.rules import GroupInviteRule
from tg_lottery.telegram.creator_publication import CreatorPublisher, confirmation_view
from tg_lottery.telegram.creator_rule_review import PAGE_UTF16_UNITS, rule_review_pages
from tg_lottery.telegram.draft_config import new_draft


def _review_data():
    return new_draft(chat_id=-1001) | {
        "participation_rules_version": None,
        "title": "阅读活动",
        "participation_groups": [{"id": -1001, "type": "supergroup", "title": "读书群"}],
        "required_chats": [{"id": -1002, "type": "channel", "title": "公告频道"}],
        "bonus_types": ["invite"],
        "group_invite_rules": {
            "-1001": GroupInviteRule(invites_per_cycle=3).model_dump(mode="json")
        },
        "chat_message_thresholds": {"-1001": 2},
        "invite_retention_hours": 0,
        "prizes": [{"name": "阅读奖", "winner_count": 1}],
    }


def test_review_shows_exact_group_rules_and_all_qualification_conditions():
    data = _review_data()
    text = "".join(rule_review_pages(data))
    assert text.startswith("📜 发布前完整规则 · 未发布\n\n")
    assert "编辑草稿" not in text and "下一项\uff1a" not in text
    for expected in ("读书群", "公告频道", "每邀请 3 个", "至少 2 条", "立即核验", "阅读奖"):
        assert expected in text
    assert "-1001" not in text and "-1002" not in text
    assert confirmation_view(data)[1][0][0][1] == "draft_publish_rules"


def test_long_rule_review_pages_preserve_all_content_and_telegram_size_limit():
    description = "📚长说明" * 1000
    pages = rule_review_pages(_review_data() | {"description": description})
    assert len(pages) > 1
    assert description in "".join(pages)
    assert all(len(page.encode("utf-16-le")) // 2 <= PAGE_UTF16_UNITS for page in pages)


async def test_reading_rules_preserves_business_config_and_publish_confirmation():
    data = _review_data()
    sessions, renderer = ThresholdSessions(data), RecordingRenderer()
    sessions.state = "draft:publish_confirm"
    publisher = object.__new__(CreatorPublisher)
    publisher._sessions, publisher._renderer = sessions, renderer
    query = SimpleNamespace(from_user=_message().from_user, message=_message())
    await publisher.show_rules(query, SimpleNamespace(action="draft_publish_rules"))
    assert sessions.state == "draft:publish_confirm"
    assert {
        key: value for key, value in sessions.data.items() if key != "publish_rules_page"
    } == data
    assert renderer.rows[-1] == [("返回发布确认", "draft_publish")]
    assert "每邀请 3 个" in renderer.text
