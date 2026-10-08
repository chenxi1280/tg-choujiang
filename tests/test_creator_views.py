from tg_lottery.telegram.creator_views import dashboard_rows, preview_text
from tg_lottery.telegram.draft_config import new_draft


def test_preview_is_a_business_card_with_waiting_fields() -> None:
    text = preview_text(new_draft())

    assert text.startswith("创建抽奖 · 编辑草稿")
    assert "🎰 抽奖标题" in text and "【等待设置】" in text
    assert "🏞️ 封面设置" in text
    assert "🪪 用户限制" in text
    assert "🌏️ 地区限制" in text
    assert "👥 参与活动的群" in text
    assert "📮 准入条件" in text
    assert "🎁 奖品内容" in text
    assert "⚠️ 抽奖说明" in text
    assert "🔋 抽奖加成" in text
    assert "🌀 开奖时间" in text
    assert "🎅 发奖人员" in text
    assert "UTC" not in text
    assert "activity_preset" not in text


def test_dashboard_separates_required_settings_bonus_and_submission():
    rows = dashboard_rows()
    labels = [label for row in rows for label, _ in row]
    actions = [action for row in rows for _, action in row]
    assert len(labels) == 10
    assert "加成设置" in labels
    assert "活跃加成\uff08人品\uff09" not in labels
    assert "邀请加成" not in labels
    assert "清空配置" not in labels
    assert "draft_conditions" in actions
    assert rows[-1] == [("返回首页\uff08草稿保留\uff09", "draft_home")]
