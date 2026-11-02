"""Shared labels for observed membership and bot relationship states."""


def condition_label(observation):
    if observation.get("observed_status") == "join_request_pending":
        return "⏳ 等待管理员批准"
    return {"eligible": "✅ 已完成", "ineligible": "❌ 未完成"}.get(
        str(observation.get("state", "unknown")), "⏳ 暂时无法检查"
    )


def condition_line(target, observation, *, title=None):
    action = "关注" if target.chat_type == "channel" else "加入"
    return f"{condition_label(observation)} · {action}「{title or target.title}」"


def condition_header(completed, total, *, source):
    return f"入群/关注条件：已完成 {completed}/{total} 项（{source}）"


def bot_condition_label(status):
    return {
        "bot_started": "✅ 已启动机器人",
        "bot_not_started": "❌ 尚未启动机器人",
        "bot_blocked": "❌ 已屏蔽机器人，请解除屏蔽后重新进入",
        "bot_relationship_unknown": "⏳ 暂时无法确认机器人启动状态，请重新进入检查",
    }[status]
