import pytest
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.telegram.ui_style import plain_label, style_keyboard, style_label


def test_semantic_icons_preserve_numeric_density_and_selected_state():
    assert style_label("抽奖加成票(发言)") == "🌱 抽奖加成票(发言)"
    assert style_label("邀请加成票") == "🤝 邀请加成票"
    assert style_label("发起抽奖") == "\u2795 发起抽奖"
    assert style_label("创建抽奖") == "\u2795 创建抽奖"
    assert style_label("返回首页") == "🏠 返回首页"
    assert style_label("✅ 发言数") == "✅ 发言数"
    for text in (
        "5条/天",
        "8条/天",
        "10条/天",
        "✓ 5条/天",
        "消息数 \u22121条",
        "自定义 · 8条",
        "\uff0b1条",
    ):
        assert style_label(text, action="draft_gate:quick:daily_messages:8") == text
    assert plain_label("✅ 🚪 发言数") == "发言数"
    assert plain_label("↩️ 返回活动") == "返回活动"
    for action, icon in (("m:gg:ref:-1", "🚪"), ("m:ad:ref:-1", "🌱"), ("m:id:ref:-1", "🤝")):
        assert style_label("A群", action=action) == icon + " A群"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("✨ 我参加的抽奖", "🙋 我参加的抽奖"),
        ("✨ 我发起的抽奖", "🗂️ 我发起的抽奖"),
        ("✨ 继续创建抽奖", "📝 继续创建抽奖"),
        ("✨ 活动标题", "🏷️ 活动标题"),
        ("✨ 预览并发布", "👁️ 预览并发布"),
        ("✨ 确认发布抽奖", "🚀 确认发布抽奖"),
        ("🤝 添加到群组", "👥 添加到群组"),
        ("🤝 添加到频道", "📡 添加到频道"),
        ("🧭 用户限制", "🪪 用户限制"),
        ("📖 参加与条件", "🧭 参加与条件"),
        ("🪪 更多设置", "⚙️ 更多设置"),
        ("🪪 个人设置", "👤 个人设置"),
        ("🌱 加成票设置", "📈 加成票设置"),
        ("🚪 自定义每日条数", "💬 自定义每日条数"),
        ("🚪 自定义连续天数", "📆 自定义连续天数"),
        ("📋 搜索群", "🔎 搜索群"),
        ("📋 复制邀请链接", "📋 复制邀请链接"),
        ("📷 上传封面", "📤 上传封面"),
        ("📷 替换封面", "♻️ 替换封面"),
        ("📷 封面与说明", "🖼️ 封面与说明"),
        ("📝 抽奖说明", "📃 抽奖说明"),
        ("🧑‍💼 开奖与领奖", "🏆 开奖与领奖"),
        ("🧹 删除指定奖品", "🗑️ 删除指定奖品"),
        ("🧹 移除本群门槛", "\u2796 移除本群门槛"),
        ("📋 上一页", "◀️ 上一页"),
        ("📋 下一页", "▶️ 下一页"),
        ("↩ 返回首页", "🏠 返回首页"),
        ("🔄 创建中 · A群", "⏳ 创建中 · A群"),
        ("🧾 等待中", "⏳ 等待中"),
        ("🧾 有效", "🟢 有效"),
        ("🧾 未计入", "🚫 未计入"),
    ],
)
def test_existing_system_badges_follow_the_specific_function(label, expected):
    assert style_label(label) == expected
    assert style_label(expected) == expected
    assert plain_label(expected) == plain_label(label)


def test_opaque_labels_and_selected_markers_are_preserved():
    assert style_label("🌱 用户自己的活动标题", action="m:detail:ref") == "🌱 用户自己的活动标题"
    assert style_label("🌱 有效活动标题", action="m:detail:ref") == "🌱 有效活动标题"
    assert style_label("✅ 🤝 添加到频道") == "✅ 🤝 添加到频道"
    assert style_label("⬜️ 🌱 抽奖加成票(发言)") == "⬜️ 🌱 抽奖加成票(发言)"


def test_keyboard_styling_is_idempotent_and_preserves_all_action_payloads():
    original = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="邀请加成票", callback_data="member:bonus_invites:ref:0"),
                InlineKeyboardButton(
                    text="复制邀请链接", copy_text=CopyTextButton(text="https://t.me/synthetic")
                ),
            ],
            [InlineKeyboardButton(text="去本群发言", url="https://t.me/synthetic_group")],
        ]
    )
    changed = style_keyboard(original)
    assert style_keyboard(changed) == changed
    assert [len(row) for row in changed.inline_keyboard] == [2, 1]
    for before, after in zip(
        [b for row in original.inline_keyboard for b in row],
        [b for row in changed.inline_keyboard for b in row],
        strict=True,
    ):
        assert before.model_dump(exclude={"text"}) == after.model_dump(exclude={"text"})
    assert original.inline_keyboard[0][0].text == "邀请加成票"
