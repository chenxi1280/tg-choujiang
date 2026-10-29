from typing import Any

from tg_lottery.telegram.message_delete_permissions import deletion_permission_text

ButtonRows = list[list[tuple[str, str]]]

MESSAGE_DELAYS: dict[str, int | None] = {
    "10s": 10,
    "15s": 15,
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
    15: "15 秒",
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
        f"🔔 临时提示自动删除\n\n扩展说明: {_explanation_label(data)}\n"
        f"删除报名命令: {command}\n临时提示保留: {delay}\n\n"
        "精简说明仍保留资格反馈、中奖结果和领奖联系人。\n"
        "📊 主动进度查询固定20秒删除；完整任务面板、公告和结果保留。"
    ) + deletion_permission_text(data)


def message_policy_summary(data: dict[str, Any]) -> str:
    command = "删除报名命令" if data.get("delete_trigger_message") else "保留报名命令"
    delay = _DELAY_LABELS[data.get("completion_delete_after_seconds")]
    return f"扩展说明 {_explanation_label(data)}; {command}; 临时提示 {delay}"


def _explanation_label(data: dict[str, Any]) -> str:
    return "显示" if data.get("message_tips", True) else "精简"


def message_settings_rows(data: dict[str, Any]) -> ButtonRows:
    expanded = bool(data.get("message_tips", True))
    rows = [
        [
            (("✅ " if expanded else "") + "显示扩展说明", "draft_messages_on"),
            (("✅ " if not expanded else "") + "精简说明", "draft_messages_off"),
        ],
        [
            ("删除报名命令", "draft_messages_trigger_on"),
            ("保留报名命令", "draft_messages_trigger_off"),
        ],
        [("15秒", "draft_messages_delay_15s"), ("20秒", "draft_messages_delay_20s")],
        [("不删除", "draft_messages_delay_never"), ("更多时长", "draft_messages_more")],
        [("↩ 返回上一层", "draft_back")],
    ]

    selected = {
        "draft_messages_trigger_on"
        if data.get("delete_trigger_message")
        else "draft_messages_trigger_off"
    }
    selected.update(
        "draft_messages_delay_" + key
        for key, value in MESSAGE_DELAYS.items()
        if value == data.get("completion_delete_after_seconds")
    )
    return [
        [(("✅ " if action in selected else "") + label, action) for label, action in row]
        for row in rows
    ]


async def show_more_delays(flow, query, grant):
    data = await flow._set_state(query, "draft:idle")
    buttons = [
        (
            ("✅ " if seconds == data.get("completion_delete_after_seconds") else "")
            + _DELAY_LABELS[seconds],
            "draft_messages_delay_" + key,
        )
        for key, seconds in MESSAGE_DELAYS.items()
        if seconds not in {None, 15, 20}
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append([("返回消息提示", "draft_messages")])
    await flow._render(query, data=data, text=message_settings_text(data), rows=rows)
