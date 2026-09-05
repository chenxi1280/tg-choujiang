"""Creator-facing explanations; the complete remote error stays in the outbox record."""

_KNOWN_ERRORS = (
    (("chat not found",), "机器人无法找到该群，请确认群绑定正确且机器人仍在群内。"),
    (
        ("not enough rights", "chat_admin_required", "have no rights", "not an administrator"),
        "机器人缺少发布权限，请在该群的管理员设置中检查发送消息权限。",
    ),
    (("kicked", "bot is not a member"), "机器人已不在该群，请重新添加机器人并设置管理员权限。"),
    (("can't parse entities",), "活动文案格式无法被 Telegram 解析，请检查文案格式。"),
    (
        ("message is too long", "caption is too long"),
        "活动文案超过 Telegram 长度限制，请缩短文案。",
    ),
    (("timeout", "timed out"), "请求超时，尚未确认群内是否已经发布，请先检查群消息。"),
)


def publication_error_text(error: str) -> str:
    normalized = error.lower()
    for patterns, explanation in _KNOWN_ERRORS:
        if any(pattern in normalized for pattern in patterns):
            return explanation
    return "具体原因尚未识别，完整错误已记录，请联系维护人员排查。"
