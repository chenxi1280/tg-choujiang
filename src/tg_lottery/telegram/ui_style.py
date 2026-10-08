"""Function-specific presentation icons without changing routes or user content."""

import re

from aiogram.types import InlineKeyboardMarkup

# Ordered from specific actions/statuses to their broader objects.
LABEL_ICONS = (
    (("返回首页",), "🏠"),
    (("返回", "← 返回", "← 上一步"), "↩️"),
    (("取消", "放弃本群", "放弃门槛", "放弃加成", "放弃修改"), "✖️"),
    (("继续创建", "继续草稿", "继续当前草稿", "创建/继续", "继续原字段", "继续编辑"), "📝"),
    (("保存", "本群暂存", "暂存", "保留当前", "保留本次"), "💾"),
    (
        (
            "确认取消",
            "确认清空",
            "确认删除",
            "确认放弃",
            "确认移除",
            "确认覆盖",
            "立即开奖",
            "提前截止",
        ),
        "⚠️",
    ),
    (("清空", "清除"), "🧹"),
    (("移除",), "➖"),
    (("删除",), "🗑️"),
    (("发布中", "创建中", "待开奖", "待核查", "待验证"), "⏳"),
    (("进行中",), "🟢"),
    (("已取消",), "🚫"),
    (("已开奖",), "🏆"),
    (("已结束",), "🏁"),
    (("我参加", "报名记录", "查看我的参与"), "🙋"),
    (("我发起", "我可管理", "抽奖管理"), "🗂️"),
    (("活动标题", "抽奖标题", "设置活动标题"), "🏷️"),
    (("查看发布状态", "投递状态"), "📊"),
    (("预览", "查看发布"), "👁️"),
    (("发布", "确认发布"), "🚀"),
    (("创建", "发起抽奖"), "➕"),
    (
        (
            "添加到群",
            "加入群聊",
            "加入群组",
            "参与群",
            "修改参与群",
            "选择参与群",
            "选择参与活动的群",
        ),
        "👥",
    ),
    (("添加到频道", "关注频道", "额外关注/加入"), "📡"),
    (("去本群", "发言数", "自定义每日"), "💬"),
    (("自定义连续",), "📆"),
    (("邀请与活跃", "我的加成", "抽奖加成", "加成设置", "加成明细"), "📈"),
    (("邀请记录", "我的邀请记录", "查看邀请记录"), "🧾"),
    (("邀请入口", "我的邀请链接", "查看个人邀请入口"), "🔗"),
    (("邀请", "我的邀请", "有效邀请", "打开邀请", "查看个人邀请", "设置邀请"), "🤝"),
    (("活跃", "人品规则", "人品加成", "设置活跃"), "🌱"),
    (("人品排行",), "🏅"),
    (("人品",), "🌱"),
    (("开启本加成",), "▶️"),
    (("关闭本加成",), "⏸️"),
    (("抽奖门槛", "查看门槛", "设置抽奖门槛"), "🚪"),
    (("参与条件", "查看参与条件", "参加与条件", "额外关注", "额外条件", "添加准入条件"), "🧭"),
    (
        ("验证资格", "重新核验", "登记参与", "我已阅读", "报名本次", "点击参与抽奖", "参与抽奖"),
        "🛂",
    ),
    (("用户限制", "用户名"), "🪪"),
    (("设置地区", "设置归属", "设置本人地区", "归属地区"), "🌐"),
    (("领奖说明",), "📮"),
    (("奖品", "添加奖品", "继续新增奖品", "保留奖品"), "🎁"),
    (("领奖", "开奖结果", "开奖与领奖"), "🏆"),
    (("上传封面",), "📤"),
    (("替换封面",), "♻️"),
    (("封面",), "🖼️"),
    (("抽奖说明", "使用默认说明"), "📃"),
    (("开奖时间", "设置开奖", "调整开奖"), "⏰"),
    (("使用指南", "使用说明频道"), "📖"),
    (("完整规则", "查看规则", "查看全部规则"), "📜"),
    (("查看活动状态与规则", "查看本场详情"), "📄"),
    (("查看当前活动状态", "查看本场投递状态", "查看我的进度"), "📊"),
    (("刷新", "重新检测", "重试"), "🔄"),
    (("搜索",), "🔎"),
    (("复制", "载入"), "📋"),
    (("全选",), "☑️"),
    (("转发", "分享", "获取分享卡片"), "🔗"),
    (("打开入群",), "👥"),
    (("个人设置",), "👤"),
    (("更多设置",), "⚙️"),
    (("查看我的用户",), "🆔"),
    (("上一页", "← 上一页", "← 上一项"), "◀️"),
    (("下一页", "下一项"), "▶️"),
    (("消息提示",), "🔔"),
    (("发奖人员", "联系发奖"), "🧑‍💼"),
    (("我的抽奖次数", "查看本期抽奖"), "🎫"),
    (("查看本人历史", "查看本场邀请", "查看本期邀请"), "🧾"),
    (("入群后立即核验", "留存 24", "留存 48"), "⏱️"),
    (("关闭该群门槛",), "⏸️"),
    (("重新输入名称",), "✏️"),
    (("保留报名命令",), "⌨️"),
    (("不删除",), "♾️"),
    (("打开本场", "启动本机器人"), "🤖"),
    (("私聊我",), "🤖"),
)
ACTION_ICONS = (
    ("draft_gate:group:", "👥"),
    ("draft_gate:input:consecutive_days", "📆"),
    ("draft_gate:input:daily_messages", "💬"),
    ("draft_gate", "🚪"),
    ("draft_bonus_invite", "🤝"),
    ("draft_bonus", "🌱"),
    ("draft_toggle_group", "👥"),
    ("draft_draw", "⏰"),
    ("nav:campaign", "🗂️"),
    ("m:detail", "📄"),
    ("m:ir", "🧾"),
    ("m:bo", "📈"),
    ("profile:region", "🌐"),
)
# Legacy badges are recognized so old system labels can be normalized too.
ICONS = (
    *dict.fromkeys(icon for _, icon in (*LABEL_ICONS, *ACTION_ICONS)),
    "✨",
    "🌱",
    "🤝",
    "🧭",
    "🚪",
    "📷",
    "📦",
    "📝",
    "🗓",
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
    "🎟",
    "⭐",
    "📣",
    "🔗",
)
ICONS = tuple(
    dict.fromkeys(value for icon in ICONS for value in (icon, icon.replace("\ufe0f", "")))
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
    marker = re.match(r"^(?:✅|⬜|✓)\ufe0f?\s+", label)
    selected = marker.group() if marker else ""
    raw = label[len(selected) :]
    text = plain_label(raw)
    if _short_number(text):
        return label
    icon = next((icon for prefixes, icon in LABEL_ICONS if text.startswith(prefixes)), "")
    if icon:
        return selected + icon + " " + text
    # Unknown/user-derived labels keep their existing decoration.
    if raw.startswith(ICONS):
        return label
    icon = next((icon for prefix, icon in ACTION_ICONS if action.startswith(prefix)), "")
    return selected + icon + " " + raw if icon else label


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
