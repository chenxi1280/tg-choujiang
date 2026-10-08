from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.telegram.ui_style import plain_label, style_keyboard, style_label


def test_semantic_icons_preserve_numeric_density_and_selected_state():
    assert style_label("活跃加成") == "🌱 活跃加成"
    assert style_label("邀请加成") == "🤝 邀请加成"
    assert style_label("返回首页") == "↩️ 返回首页"
    assert style_label("✅ 发言数") == "✅ 🚪 发言数"
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


def test_keyboard_styling_is_idempotent_and_preserves_all_action_payloads():
    original = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="邀请加成", callback_data="member:bonus_invites:ref:0"),
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
    assert original.inline_keyboard[0][0].text == "邀请加成"
