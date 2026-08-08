from tg_lottery.telegram.creator_views import dashboard_rows, preview_text
from tg_lottery.telegram.draft_config import new_draft


def test_preview_is_a_business_card_with_waiting_fields() -> None:
    text = preview_text(new_draft())

    assert text.startswith("🎰 抽奖预览")
    assert "👥 活动群" in text and "【等待设置】" in text
    assert "🏷 抽奖标题" in text
    assert "🎁 奖品内容" in text
    assert "👤 发起人" in text
    assert "UTC" not in text
    assert "activity_preset" not in text


def test_dashboard_uses_compact_three_column_business_buttons() -> None:
    rows = dashboard_rows()

    assert [label for label, _ in rows[0]] == ["活动群", "抽奖标题", "奖品内容"]
    assert [label for label, _ in rows[1]] == ["开奖时间", "领奖说明", "中奖规则"]
    assert rows[2] == [("🎉 发布抽奖", "draft_publish")]
