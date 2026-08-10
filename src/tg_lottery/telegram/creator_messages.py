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
    return f"🔔 参与完成提示\n\n删除报名命令: {command}\n完成提示保留: {delay}"


def message_policy_summary(data: dict[str, Any]) -> str:
    command = "删除报名命令" if data.get("delete_trigger_message") else "保留报名命令"
    delay = _DELAY_LABELS[data.get("completion_delete_after_seconds")]
    return f"{command}; 完成提示 {delay}"


def message_settings_rows() -> ButtonRows:
    return [
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
