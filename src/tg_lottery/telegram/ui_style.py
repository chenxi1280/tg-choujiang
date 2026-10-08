"""Presentation-only semantic icons; never change routes, payloads or user text."""

import re

from aiogram.types import InlineKeyboardMarkup

ICONS = (
    "⏳",
    "🏁",
    "✨",
    "🌱",
    "🤝",
    "🧭",
    "🚪",
    "📷",
    "📦",
    "📝",
    "🗓",
    "🧑‍💼",
    "💾",
    "↩",
    "🏠",
    "📖",
    "🔄",
    "⚠",
    "📋",
    "🧹",
    "🪪",
    "🌏",
    "🔔",
    "🏆",
    "🎁",
    "📊",
)
LABEL_ICONS = (
    (("返回", "取消输入", "取消复制", "继续编辑", "放弃本群"), "↩️"),
    (("保存", "本群暂存", "暂存", "保留当前", "保留本次"), "💾"),
    (("清空", "移除", "删除", "确认放弃", "放弃修改"), "🧹"),
    (("邀请", "我的邀请", "有效邀请", "参与群", "修改参与群", "添加到群"), "🤝"),
    (("活跃", "人品", "我的加成", "加成", "开启本加成", "关闭本加成"), "🌱"),
    (("抽奖门槛", "查看门槛", "发言数", "自定义每日", "自定义连续"), "🚪"),
    (("参与条件", "查看参与条件", "额外关注", "用户限制", "设置地区"), "🧭"),
    (("奖品", "添加奖品", "领奖", "继续新增奖品"), "📦"),
    (("封面", "上传封面", "替换封面"), "📷"),
    (("抽奖说明",), "📝"),
    (("开奖时间", "设置开奖", "调整开奖"), "🗓️"),
    (("使用指南", "完整规则", "查看规则", "参加与条件"), "📖"),
    (("刷新",), "🔄"),
    (("搜索", "复制", "载入", "全选"), "📋"),
    (("个人设置", "更多设置", "查看我的用户", "用户名"), "🪪"),
    (("发布中", "待开奖", "待核查", "待验证"), "⏳"),
    (("进行中",), "🌱"),
    (("已结束", "已开奖", "已取消", "开奖结果"), "🏁"),
    (("确认取消", "确认清空", "确认删除", "立即开奖", "提前截止"), "⚠️"),
    (("去本群", "添加到频道", "我可管理"), "🤝"),
    (("上一页", "下一页", "← 上一页"), "📋"),
    (("消息提示",), "🔔"),
    (("发奖人员", "联系发奖", "开奖与领奖"), "🧑‍💼"),
    (
        (
            "创建",
            "继续创建",
            "活动标题",
            "设置活动标题",
            "我发起",
            "我参加",
            "预览",
            "发布",
            "查看发布",
        ),
        "✨",
    ),
)
ACTION_ICONS = (
    ("draft_gate", "🚪"),
    ("draft_bonus_invite", "🤝"),
    ("draft_bonus", "🌱"),
    ("draft_toggle_group", "🤝"),
    ("draft_draw", "🗓️"),
    ("nav:campaign", "✨"),
    ("m:detail", "✨"),
    ("m:ir", "🤝"),
    ("m:bo", "🌱"),
    ("profile:region", "🌏️"),
)


def plain_label(label: str) -> str:
    text = label
    while True:
        prefix = next(
            (value for value in (*ICONS, "✅", "⬜", "✓") if text.startswith(value)), None
        )
        if prefix is None:
            return text
        text = text[len(prefix) :].lstrip("\ufe0f ")


def style_label(label: str, *, action: str = "") -> str:
    selected = next((marker for marker in ("✅ ", "⬜ ", "✓ ") if label.startswith(marker)), "")
    text = label[len(selected) :]
    if text.startswith("↩ "):
        return selected + "↩️ " + text[2:]
    if text.startswith(ICONS) or _short_number(text):
        return label
    icon = next((icon for prefixes, icon in LABEL_ICONS if text.startswith(prefixes)), "")
    icon = icon or next((icon for prefix, icon in ACTION_ICONS if action.startswith(prefix)), "")
    return selected + icon + " " + text if icon else label


def _short_number(label: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:条/天|条|天|人|次|轮)?", label)) or any(
        marker in label for marker in ("−1", "＋1", "+1", "自定义 ·")
    )


def style_keyboard(keyboard: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    if keyboard is None:
        return None
    return keyboard.model_copy(
        update={
            "inline_keyboard": [
                [
                    button.model_copy(
                        update={"text": style_label(button.text, action=button.callback_data or "")}
                    )
                    for button in row
                ]
                for row in keyboard.inline_keyboard
            ]
        }
    )


def row_keyboard(rows):
    return style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows))
