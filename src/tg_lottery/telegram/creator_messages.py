from typing import Any

ButtonRows = list[list[tuple[str, str]]]

MESSAGE_DELAYS: dict[str, int | None] = {
    "10s": 10,
    "20s": 20,
    "30s": 30,
    "1m": 60,
    "5m": 300,
    "10m": 600,
    "30m": 1800,
    "60m": 3600,
    "never": None,
}

_DELAY_LABELS = {
    10: "10 秒",
    20: "20 秒",
    30: "30 秒",
    60: "1 分钟",
    300: "5 分钟",
    600: "10 分钟",
    1800: "30 分钟",
    3600: "60 分钟",
    None: "不删除",
}


def message_settings_text(data: dict[str, Any]) -> str:
    command = "开启" if data.get("delete_trigger_message") else "关闭"
    delay = _DELAY_LABELS[data.get("completion_delete_after_seconds")]
    return (
        f"🔔 参与完成提示\n\n扩展说明: {_explanation_label(data)}\n"
        f"删除报名命令: {command}\n完成提示保留: {delay}\n\n"
        "精简说明仍保留资格反馈、中奖结果和领奖联系人。"
    )


def message_policy_summary(data: dict[str, Any]) -> str:
    command = "删除报名命令" if data.get("delete_trigger_message") else "保留报名命令"
    delay = _DELAY_LABELS[data.get("completion_delete_after_seconds")]
    return f"扩展说明 {_explanation_label(data)}; {command}; 完成提示 {delay}"


def _explanation_label(data: dict[str, Any]) -> str:
    return "显示" if data.get("message_tips", True) else "精简"


def message_settings_rows(data: dict[str, Any]) -> ButtonRows:
    expanded = bool(data.get("message_tips", True))
    return [
        [
            (("✅ " if expanded else "") + "显示扩展说明", "draft_messages_on"),
            (("✅ " if not expanded else "") + "精简说明", "draft_messages_off"),
        ],
        [
            ("删除报名命令", "draft_messages_trigger_on"),
            ("保留报名命令", "draft_messages_trigger_off"),
        ],
        [("10秒", "draft_messages_delay_10s"), ("20秒", "draft_messages_delay_20s")],
        [("30秒", "draft_messages_delay_30s"), ("1分钟", "draft_messages_delay_1m")],
        [("5分钟", "draft_messages_delay_5m"), ("10分钟", "draft_messages_delay_10m")],
        [("30分钟", "draft_messages_delay_30m"), ("60分钟", "draft_messages_delay_60m")],
        [("不删除", "draft_messages_delay_never")],
        [("↩ 返回上一层", "draft_back")],
    ]
