from tg_lottery.telegram.creator_views import dashboard_rows, preview_text
from tg_lottery.telegram.draft_config import new_draft


def test_preview_is_a_business_card_with_waiting_fields() -> None:
    text = preview_text(new_draft())

    assert text.startswith("🎰 抽奖预览")
    assert "🎰 抽奖标题" in text and "【等待设置】" in text
    assert "🏞️ 封面设置" in text
    assert "🪪 用户限制" in text
    assert "🌏️ 地区限制" in text
    assert "📮 参与条件" in text
    assert "🎁 奖品内容" in text
    assert "⚠️ 抽奖说明" in text
    assert "🔋 人品加成" in text
    assert "🌀 开奖方式" in text
    assert "🎅 发奖人员" in text
    assert "UTC" not in text
    assert "activity_preset" not in text


def test_dashboard_uses_compact_three_column_business_buttons() -> None:
    rows = dashboard_rows()

    assert [label for label, _ in rows[0]] == ["活动标题", "上传封面", "用户限制"]
    assert [label for label, _ in rows[1]] == ["参与条件", "奖品内容", "抽奖说明"]
    assert [label for label, _ in rows[2]] == ["人品加成", "开奖方式", "发奖人员"]
    assert rows[3] == [
        ("消息提示", "draft_messages"),
        ("🎉 发布抽奖", "draft_publish"),
    ]
